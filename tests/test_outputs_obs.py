import json
import logging
import os
import threading
import time
from types import SimpleNamespace

import pytest
import websocket
from obsws_python.error import OBSSDKRequestError

import obs_websocket


class FakeWebSocket:
    def __init__(self):
        self.closed = False

    def shutdown(self):
        self.closed = True


def new_base(**kwargs):
    """Stands in for ReqClient.base_client: connection parameters + websocket."""
    return SimpleNamespace(ws=FakeWebSocket(), **kwargs)


class FakeClient:
    """Stands in for obsws_python.ReqClient."""

    def __init__(self, delay=0.0, gate=None):
        self.delay = delay
        self.gate = gate
        self.started = threading.Event()
        self.calls = []
        self.errors = []  # exceptions to raise on the next calls, in order
        self.base_client = new_base(host="obs-pc", port=4455, password="pw", timeout=3)
        self.disconnected = False

    def set_input_settings(self, name, settings, overlay):
        self.started.set()
        if self.gate is not None:
            assert self.gate.wait(5)
        if self.delay:
            time.sleep(self.delay)
        if self.base_client.ws.closed:
            # like websocket-client after the socket was closed
            raise websocket.WebSocketConnectionClosedException("closed")
        if self.errors:
            raise self.errors.pop(0)
        self.calls.append((name, settings["text"], overlay))

    def get_scene_list(self):
        self.calls.append(("get_scene_list",))
        return SimpleNamespace(scenes=[])

    def disconnect(self):
        self.disconnected = True
        # like ReqClient.disconnect(): later requests fail on the closed socket
        self.base_client.ws.shutdown()


@pytest.fixture(autouse=True)
def idle_worker():
    worker = obs_websocket._text_worker
    worker.error_log.reset()
    worker._retry_failures = 0
    yield
    worker.clear()
    assert worker.wait_idle(10)


def flush():
    assert obs_websocket._text_worker.wait_idle(10)


def test_update_text_source_sets_the_text_in_the_background():
    client = FakeClient()
    obs_websocket.update_text_source(client, "Home Score", "12")
    flush()
    assert client.calls == [("Home Score", "12", True)]


def test_update_never_blocks_and_keeps_latest_text_per_source():
    # the old code called OBS on the GUI thread with a 10 s timeout
    client = FakeClient(delay=0.25)
    start = time.monotonic()
    obs_websocket.update_text_source(client, "Home Score", "3")
    for second in range(59, -1, -1):
        obs_websocket.update_text_source(client, "Time", f"0:{second:02}")
    assert time.monotonic() - start < 1.0  # blocking would take 15 s+

    flush()
    assert ("Home Score", "3", True) in client.calls
    assert client.calls[-1] == ("Time", "0:00", True)
    assert len(client.calls) <= 10  # of 61 submitted (typically 3)


@pytest.mark.parametrize("source_name", ["", None])
def test_boxes_without_a_source_name_are_skipped(source_name):
    client = FakeClient()
    obs_websocket.update_text_source(client, source_name, "12")
    obs_websocket.update_text_source(None, "Home Score", "12")
    flush()
    assert client.calls == []


def test_failed_request_is_logged_and_others_still_update(caplog):
    client = FakeClient()
    client.errors = [OBSSDKRequestError("SetInputSettings", 600, "No source")]
    with caplog.at_level(logging.ERROR):
        obs_websocket.update_text_source(client, "Missing", "1")
        obs_websocket.update_text_source(client, "Time", "1:00")
        flush()
    assert client.calls == [("Time", "1:00", True)]
    assert "No source" in caplog.text


def test_a_missing_text_source_shows_in_the_status_until_closed():
    import output_status

    obs_websocket._text_worker.reset_status()
    client = FakeClient()
    client.errors = [
        OBSSDKRequestError(
            "SetInputSettings", 600, "No source was found by the name of `Clock`."
        )
    ]
    obs_websocket.update_text_source(client, "Clock", "1:00")
    obs_websocket.update_text_source(client, "Home Score", "12")
    flush()
    worker = obs_websocket.obs_output_status()
    assert (worker.ok_keys, worker.failing_keys) == (1, 1)
    assert worker.last_error.code == 600
    status = output_status.obs_status("obs-pc:4455", None, None, worker)
    assert status.state == "warning"
    assert status.detail == "1 of 2 failing: OBS has no text source “Clock”"

    # a closed connection's text sources are forgotten; its updates are skipped
    obs_websocket.close_obs_websocket(client)
    worker = obs_websocket.obs_output_status()
    assert (worker.ok_keys, worker.failing_keys, worker.last_error) == (0, 0, None)
    sent = worker.sent
    obs_websocket.update_text_source(client, "Clock", "0:59")
    flush()
    assert obs_websocket.obs_output_status().sent == sent


def test_lost_connection_is_reestablished(monkeypatch, caplog):
    client = FakeClient()
    old_ws = client.base_client.ws
    client.errors = [websocket.WebSocketConnectionClosedException("closed")]
    fresh_base = new_base(host="obs-pc", port=4455, password="pw", timeout=3)
    created = []

    def fake_req_client(**kwargs):
        created.append(kwargs)
        return SimpleNamespace(base_client=fresh_base)

    monkeypatch.setattr(obs_websocket.obs, "ReqClient", fake_req_client)
    with caplog.at_level(logging.INFO):
        obs_websocket.update_text_source(client, "Time", "1:00")
        flush()

    assert created == [{"host": "obs-pc", "port": 4455, "password": "pw", "timeout": 3}]
    assert old_ws.closed
    assert client.base_client is fresh_base
    assert client.calls == [("Time", "1:00", True)]
    assert "reconnected" in caplog.text


def test_a_close_frame_from_obs_is_a_lost_connection(monkeypatch):
    # OBS shutting down (or restarting its websocket server) sends a close frame:
    # the next request reads an empty message, which obsws-python fails to parse.
    # That update used to be dropped, and it wasn't sent again until it changed.
    client = FakeClient()
    client.errors = [json.JSONDecodeError("Expecting value", "", 0)]
    created = []

    def fake_req_client(**kwargs):
        created.append(kwargs)
        return SimpleNamespace(base_client=new_base(**kwargs))

    monkeypatch.setattr(obs_websocket.obs, "ReqClient", fake_req_client)
    obs_websocket.update_text_source(client, "Home Score", "12")
    flush()
    assert len(created) == 1
    assert client.calls == [("Home Score", "12", True)]


def test_unreachable_obs_is_retried_until_it_is_back(monkeypatch, caplog):
    client = FakeClient()
    client.errors = [ConnectionResetError("reset")]
    attempts = []

    def fake_req_client(**kwargs):
        attempts.append(kwargs)
        if len(attempts) < 3:
            raise ConnectionRefusedError("OBS is closed")
        return SimpleNamespace(base_client=new_base(**kwargs))

    monkeypatch.setattr(obs_websocket.obs, "ReqClient", fake_req_client)
    monkeypatch.setattr(obs_websocket._text_worker, "_initial_backoff", 0.01)
    with caplog.at_level(logging.ERROR):
        obs_websocket.update_text_source(client, "Time", "1:00")
        obs_websocket.update_text_source(client, "Time", "0:59")
        deadline = time.monotonic() + 10
        while not client.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        flush()
    assert len(attempts) == 3
    assert client.calls == [("Time", "0:59", True)]
    assert "OBS is closed" in caplog.text


def test_closed_client_is_not_updated_or_reconnected():
    client = FakeClient()
    obs_websocket.close_obs_websocket(client)
    assert client.disconnected
    obs_websocket.update_text_source(client, "Time", "1:00")
    flush()
    assert client.calls == []


def test_a_client_closed_while_an_update_waits_is_not_reconnected(monkeypatch):
    # The update passed the "closed" check, then waited for the client's lock
    # while close_obs_websocket disconnected the client. It used to see the
    # closed socket as a lost connection and reconnect the closed client.
    client = FakeClient()
    created = []

    def fake_req_client(**kwargs):
        created.append(kwargs)
        return SimpleNamespace(base_client=new_base(**kwargs))

    monkeypatch.setattr(obs_websocket.obs, "ReqClient", fake_req_client)
    real_client_lock = obs_websocket.client_lock
    waiting = threading.Event()

    def spying_client_lock(obs_client):
        if threading.current_thread().name == "output-OBS":
            waiting.set()  # past the first "closed" check
        return real_client_lock(obs_client)

    monkeypatch.setattr(obs_websocket, "client_lock", spying_client_lock)
    with real_client_lock(client):  # e.g. a scene function running
        obs_websocket.update_text_source(client, "Time", "1:00")
        assert waiting.wait(5)
        obs_websocket.close_obs_websocket(client)
    flush()
    assert client.disconnected
    assert created == []
    assert client.calls == []


def test_scene_functions_wait_for_a_text_update_in_flight():
    # the websocket client isn't thread-safe: requests from the GUI thread
    # (scene creation) and the output thread must not interleave
    gate = threading.Event()
    client = FakeClient(gate=gate)
    obs_websocket.update_text_source(client, "Time", "1:00")
    assert client.started.wait(5)

    done = threading.Event()
    thread = threading.Thread(
        target=lambda: (obs_websocket.get_all_sources(client), done.set())
    )
    thread.start()
    assert not done.wait(0.2)  # blocked on the client's lock
    gate.set()
    assert done.wait(5)
    thread.join(5)
    assert client.calls == [("Time", "1:00", True), ("get_scene_list",)]


def test_obsws_does_not_log_the_password():
    # obsws-python logs "Connecting with parameters: ... password='...'" at INFO
    for name in ("obsws_python", "obsws_python.baseclient.ObsClient"):
        assert logging.getLogger(name).getEffectiveLevel() > logging.INFO


def test_open_obs_websocket(monkeypatch):
    calls = []

    class FakeReqClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def get_version(self):
            return SimpleNamespace(obs_version="31.0.0")

    monkeypatch.setattr(obs_websocket.obs, "ReqClient", FakeReqClient)
    client = obs_websocket.open_obs_websocket(
        {"ip": "localhost", "port": "4455", "password": "pw"}
    )
    assert isinstance(client, FakeReqClient)
    assert calls == [
        {
            "host": "localhost",
            "port": "4455",
            "password": "pw",
            "timeout": obs_websocket.OBS_TIMEOUT,
        }
    ]

    def refuse(**kwargs):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(obs_websocket.obs, "ReqClient", refuse)
    assert (
        obs_websocket.open_obs_websocket({"ip": "x", "port": "1", "password": ""})
        is None
    )


def test_create_obs_scene_points_image_sources_at_the_bundled_images(caplog):
    class SceneClient:
        def __init__(self):
            self.inputs = []
            self.filters = []

        def get_scene_list(self):
            return SimpleNamespace(scenes=[])

        def get_input_list(self):
            return SimpleNamespace(inputs=[])

        def create_scene(self, name):
            pass

        def create_input(self, sceneName, inputName, inputKind, inputSettings, **kw):
            self.inputs.append(inputSettings)

        def get_scene_item_id(self, scene, name):
            return SimpleNamespace(scene_item_id=1)

        def set_scene_item_transform(self, scene, item_id, transform):
            pass

        def create_source_filter(self, source, name, kind, settings):
            self.filters.append(settings)

    client = SceneClient()
    with caplog.at_level(logging.ERROR, logger=obs_websocket.logger.name):
        obs_websocket.create_obs_scene_from_export(client, "ScoreSight")
    assert not caplog.records

    images = [s["file"] for s in client.inputs if "file" in s]
    images += [s["image_path"] for s in client.filters if "image_path" in s]
    assert images
    for image in images:
        assert os.path.isfile(image), image


class ExistingSceneClient:
    """An OBS with the scenes "Main" and "ScoreSight" (one text source)."""

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def get_scene_list(self):
        if self.fail:
            raise ConnectionError("OBS is gone")
        return SimpleNamespace(
            scenes=[{"sceneName": "Main"}, {"sceneName": "ScoreSight"}]
        )

    def get_current_program_scene(self):
        return SimpleNamespace(current_program_scene_name="Main")

    def get_current_preview_scene(self):
        return SimpleNamespace(current_preview_scene_name="Main")

    def get_scene_item_list(self, scene):
        return SimpleNamespace(scene_items=[{"sceneItemId": 1, "sourceName": "Clock"}])

    def get_group_list(self):
        return SimpleNamespace(groups=[])

    def get_input_list(self):
        return SimpleNamespace(inputs=[])

    def __getattr__(self, name):
        # create_scene, create_input, remove_input...: recorded
        if name.startswith(("create_", "remove_", "set_", "get_scene_item_id")):
            return lambda *args, **kwargs: (
                self.calls.append(name) or SimpleNamespace(scene_item_id=1)
            )
        raise AttributeError(name)


def test_create_obs_scene_keeps_an_existing_scene_without_recreate():
    # The "Recreate" checkbox was never read: an existing scene was always
    # emptied and made again
    client = ExistingSceneClient()
    outcome = obs_websocket.create_obs_scene_from_export(
        client, "ScoreSight", recreate=False
    )
    assert outcome == obs_websocket.SCENE_KEPT
    assert client.calls == []  # left as it is

    outcome = obs_websocket.create_obs_scene_from_export(client, "ScoreSight")
    assert outcome == obs_websocket.SCENE_RECREATED
    # "Clock" is in the scene "Main" too: only its scene item goes
    assert client.calls[0] == "remove_scene_item"
    assert "remove_input" not in client.calls
    assert "create_input" in client.calls

    client.calls.clear()
    outcome = obs_websocket.create_obs_scene_from_export(
        client, "Court 2", recreate=False
    )
    assert outcome == obs_websocket.SCENE_CREATED
    assert client.calls[0] == "create_scene"


def test_create_obs_scene_says_when_obs_cannot_be_asked(caplog):
    client = ExistingSceneClient(fail=True)
    with caplog.at_level(logging.ERROR, logger=obs_websocket.logger.name):
        outcome = obs_websocket.create_obs_scene_from_export(client, "ScoreSight")
    assert outcome == obs_websocket.SCENE_FAILED
    assert client.calls == []
    assert "scene list" in caplog.text


class FakeOBS:
    """An OBS with scenes, groups and inputs, enough for Create OBS Scene.

    Inputs are global, as in OBS: RemoveInput deletes an input and its items in
    every scene. With orphans_destroyed, removing the last scene item of an
    input deletes the input too (what OBS does with an unreferenced source).
    """

    def __init__(self, scenes, inputs, groups=None, orphans_destroyed=False):
        self.scenes = {name: list(items) for name, items in scenes.items()}
        self.groups = {name: list(items) for name, items in (groups or {}).items()}
        self.inputs = dict(inputs)  # {name: kind}
        self.settings = {name: {"user": True} for name in inputs}
        self.orphans_destroyed = orphans_destroyed
        self.calls = []
        self.fail_on = set()  # (method, name) pairs that raise
        self._next_id = 100

    def item(self, name):
        self._next_id += 1
        return {
            "sceneItemId": self._next_id,
            "sourceName": name,
            "inputKind": self.inputs.get(name),
        }

    def _check(self, method, name):
        self.calls.append((method, name))
        if (method, name) in self.fail_on:
            raise OBSSDKRequestError(method, 600, f"cannot {method} {name}")

    def _used(self, name):
        return any(
            item["sourceName"] == name
            for items in [*self.scenes.values(), *self.groups.values()]
            for item in items
        )

    def get_scene_list(self):
        return SimpleNamespace(scenes=[{"sceneName": name} for name in self.scenes])

    def get_group_list(self):
        return SimpleNamespace(groups=list(self.groups))

    def get_group_scene_item_list(self, name):
        return SimpleNamespace(scene_items=list(self.groups[name]))

    def get_scene_item_list(self, scene):
        return SimpleNamespace(scene_items=list(self.scenes[scene]))

    def get_current_program_scene(self):
        return SimpleNamespace(current_program_scene_name="Main")

    def get_current_preview_scene(self):
        return SimpleNamespace(current_preview_scene_name="Main")

    def get_input_list(self):
        return SimpleNamespace(
            inputs=[
                {"inputName": name, "inputKind": kind}
                for name, kind in self.inputs.items()
            ]
        )

    def remove_scene_item(self, scene, item_id):
        item = next(i for i in self.scenes[scene] if i["sceneItemId"] == item_id)
        self._check("remove_scene_item", item["sourceName"])
        self.scenes[scene].remove(item)
        name = item["sourceName"]
        if self.orphans_destroyed and name in self.inputs and not self._used(name):
            del self.inputs[name]

    def remove_input(self, name):
        self._check("remove_input", name)
        del self.inputs[name]
        for items in [*self.scenes.values(), *self.groups.values()]:
            items[:] = [item for item in items if item["sourceName"] != name]

    def create_scene(self, name):
        self.scenes[name] = []

    def create_input(self, sceneName, inputName, inputKind, inputSettings, **kw):
        self._check("create_input", inputName)
        if inputName in self.inputs:
            raise OBSSDKRequestError("CreateInput", 601, "name already in use")
        self.inputs[inputName] = inputKind
        self.settings[inputName] = inputSettings
        self.scenes[sceneName].append(self.item(inputName))

    def create_scene_item(self, scene_name, source_name, enabled=None):
        self._check("create_scene_item", source_name)
        if source_name not in self.inputs:
            raise OBSSDKRequestError("CreateSceneItem", 600, "no such source")
        self.scenes[scene_name].append(self.item(source_name))

    def get_scene_item_id(self, scene, name):
        item = next(i for i in self.scenes[scene] if i["sourceName"] == name)
        return SimpleNamespace(scene_item_id=item["sceneItemId"])

    def set_scene_item_transform(self, scene, item_id, transform):
        pass

    def create_source_filter(self, source, name, kind, settings):
        self.calls.append(("create_source_filter", source))


def names_in(items):
    return [item["sourceName"] for item in items]


def test_recreate_never_deletes_a_shared_or_user_source():
    # Recreate called RemoveInput on every item of the scene: an input that
    # another scene used too was deleted from the whole scene collection
    obs = FakeOBS(
        scenes={"Main": []},
        inputs={
            "Clock": "text_ft2_source_v2",  # ScoreSight's, also in "Main"
            "Home score": "text_ft2_source_v2",  # ScoreSight's, only here
            "My Logo": "image_source",  # the user's, only here
            "Webcam": "dshow_input",  # the user's, also in "Main"
        },
    )
    obs.scenes["Main"] = [obs.item("Clock"), obs.item("Webcam")]
    obs.scenes["ScoreSight"] = [
        obs.item(name) for name in ("Clock", "Home score", "My Logo", "Webcam")
    ]
    clock_settings = obs.settings["Clock"]

    outcome = obs_websocket.create_obs_scene_from_export(obs, "ScoreSight")
    assert outcome == obs_websocket.SCENE_RECREATED

    removed = [name for method, name in obs.calls if method == "remove_input"]
    assert removed == ["Home score"]  # made by ScoreSight, used nowhere else
    # the shared and the user's sources still exist; "Main" is untouched
    assert {"Clock", "Webcam", "My Logo"} <= obs.inputs.keys()
    assert names_in(obs.scenes["Main"]) == ["Clock", "Webcam"]
    # the user's sources are no longer in the recreated scene
    names = names_in(obs.scenes["ScoreSight"])
    assert "Webcam" not in names and "My Logo" not in names
    # the kept "Clock" is added again, with its own settings and filters
    assert ("create_scene_item", "Clock") in obs.calls
    assert ("create_input", "Clock") not in obs.calls
    assert obs.settings["Clock"] is clock_settings
    assert ("create_source_filter", "Clock") not in obs.calls
    # every input of the template is in the scene, once
    assert sorted(names) == sorted(obs_websocket.scoresight_input_kinds())
    assert ("create_input", "Home score") in obs.calls


def test_recreate_works_when_obs_deletes_orphaned_inputs():
    obs = FakeOBS(
        scenes={"Main": []},
        inputs={"Clock": "text_ft2_source_v2", "Period": "text_ft2_source_v2"},
        orphans_destroyed=True,
    )
    obs.scenes["Main"] = [obs.item("Clock")]
    obs.scenes["ScoreSight"] = [obs.item("Clock"), obs.item("Period")]
    outcome = obs_websocket.create_obs_scene_from_export(obs, "ScoreSight")
    assert outcome == obs_websocket.SCENE_RECREATED
    assert "Clock" in obs.inputs
    names = names_in(obs.scenes["ScoreSight"])
    assert names.count("Clock") == 1 and names.count("Period") == 1


def test_an_input_in_a_group_counts_as_used():
    obs = FakeOBS(
        scenes={"Main": []},
        inputs={"Period": "text_ft2_source_v2"},
        groups={"Bug": []},
    )
    obs.groups["Bug"] = [obs.item("Period")]
    obs.scenes["ScoreSight"] = [obs.item("Period")]
    obs_websocket.create_obs_scene_from_export(obs, "ScoreSight")
    assert ("remove_input", "Period") not in obs.calls
    assert names_in(obs.groups["Bug"]) == ["Period"]


def test_no_input_is_deleted_when_obs_cannot_tell_where_it_is_used():
    obs = FakeOBS(scenes={"Main": []}, inputs={"Period": "text_ft2_source_v2"})
    obs.scenes["ScoreSight"] = [obs.item("Period")]

    def broken():
        raise OBSSDKRequestError("GetGroupList", 500, "broken")

    obs.get_group_list = broken
    obs_websocket.create_obs_scene_from_export(obs, "ScoreSight")
    assert ("remove_input", "Period") not in obs.calls
    assert ("remove_scene_item", "Period") in obs.calls


def test_one_item_that_cannot_be_removed_does_not_stop_the_others(caplog):
    obs = FakeOBS(
        scenes={"Main": []},
        inputs={"Clock": "text_ft2_source_v2", "My Text": "text_gdiplus_v3"},
    )
    obs.scenes["ScoreSight"] = [obs.item("My Text"), obs.item("Clock")]
    obs.fail_on.add(("remove_scene_item", "My Text"))
    with caplog.at_level(logging.ERROR, logger=obs_websocket.logger.name):
        obs_websocket.create_obs_scene_from_export(obs, "ScoreSight")
    assert ("remove_input", "Clock") in obs.calls
    assert "My Text" in caplog.text


def test_an_input_of_another_kind_with_a_template_name_is_left_alone(caplog):
    obs = FakeOBS(scenes={"Main": []}, inputs={"Clock": "image_source"})
    obs.scenes["Main"] = [obs.item("Clock")]
    obs.scenes["ScoreSight"] = []
    with caplog.at_level(logging.WARNING, logger=obs_websocket.logger.name):
        obs_websocket.create_obs_scene_from_export(obs, "ScoreSight")
    assert obs.inputs["Clock"] == "image_source"
    assert ("create_scene_item", "Clock") not in obs.calls
    assert "another kind" in caplog.text
