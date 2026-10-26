#include <windows.h>
#include <dshow.h>
#include <stdexcept>
#include <string>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;

HRESULT InitializeCOM() {
    return CoInitialize(nullptr);
}

void UninitializeCOM() {
    CoUninitialize();
}

int EnumerateVideoDevicesDShow(std::vector<std::wstring>& deviceNames) {
    ICreateDevEnum *pDevEnum = nullptr;
    IEnumMoniker *pEnum = nullptr;
    deviceNames.clear();

    HRESULT hr = CoCreateInstance(CLSID_SystemDeviceEnum, nullptr, CLSCTX_INPROC, IID_ICreateDevEnum,
        reinterpret_cast<void**>(&pDevEnum));

    if (FAILED(hr)) {
        return -1;
    }

    hr = pDevEnum->CreateClassEnumerator(CLSID_VideoInputDeviceCategory, &pEnum, 0);
    if (hr != S_OK || !pEnum) {
        // S_FALSE: there are no video input devices
        pDevEnum->Release();
        return hr == S_FALSE ? 0 : -1;
    }

    IMoniker *pMoniker = nullptr;
    ULONG i_fetched;
    while (pEnum->Next(1, &pMoniker, &i_fetched) == S_OK) {
        IPropertyBag *pPropBag;
        hr = pMoniker->BindToStorage(0, 0, IID_PPV_ARGS(&pPropBag));
        if (FAILED(hr)) {
            pMoniker->Release();
            continue;
        }
        VARIANT var;
        VariantInit(&var);

        // Get the friendly name of the device
        if (SUCCEEDED(pPropBag->Read(L"FriendlyName", &var, 0))) {
            if (var.vt == VT_BSTR && var.bstrVal != nullptr) {
                deviceNames.push_back(std::wstring(var.bstrVal));
            }
            VariantClear(&var);
        }

        pPropBag->Release();
        pMoniker->Release();
    }
    pEnum->Release();
    pDevEnum->Release();

    return (int)deviceNames.size();
}

PYBIND11_MODULE(win32DeviceEnumBind, m) {
    m.def("InitializeCOM", &InitializeCOM, "A function that initializes COM");
    m.def("UninitializeCOM", &UninitializeCOM, "A function that uninitializes COM");
    m.def("EnumerateVideoDevicesDShow", []() {
        std::vector<std::wstring> deviceNames;
        // rc < 0: CoCreateInstance / CreateClassEnumerator failed. Returning
        // the cleared name list made a COM error look like "no camera".
        // S_FALSE (no video input device) is rc 0 and an empty list.
        int rc = EnumerateVideoDevicesDShow(deviceNames);
        if (rc < 0) {
            throw std::runtime_error(
                "DirectShow video device enumeration failed");
        }
        return deviceNames;
    }, "A function that enumerates video devices");
}
