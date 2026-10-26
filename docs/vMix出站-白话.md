# 光为 OCR → vMix 出站（白话）

你要的是：**一个数据源里，24 秒、主队、客队、时间全在一起**。  
下面三种接法任选；新手用①就够。

---

## ① JSON 文件（推荐）

软件默认只写一个 `results.json`，形状是：

```json
[{ "home_score": "52", "away_score": "55", "game_clock": "01:16.7", "period": "4", "home_fouls": "3", "away_fouls": "4", "shot_clock": "10" }]
```

注意：
- 外面有一层 `[]`——vMix Data Sources 的 JSON 只认「数组」。
- **vMix 的 JSON 列名必须是英文**。软件已自动把「主队得分」等写成 `home_score` 等；标题里用这些英文列名绑定。CSV 仍可用中文表头。

### 怎么点

1. 光为 OCR → 右边 **出站 / 文本文件**
2. 选好输出文件夹，勾选 **「给 vMix 的 JSON（推荐）」**
3. 点蓝色卡片上的 **「一键复制 JSON 路径」**
4. 打开 vMix → **Data Sources** → **+** → **JSON**
5. 路径栏粘贴 → **Update** 填 1000 毫秒 → OK
6. 做标题时从这一个 Data Source 里拖 **home_score / away_score / game_clock / period / shot_clock…**，**不要**再加五个 Text 源

卡片上还有「复制示例 JSON」「打开文件夹」；下面会显示最近一次是否写出。

若以前加过旧的 JSON 源只显示一列 `a:item`：删掉该源再重新添加，或点刷新，就会看到全部英文列。

---

## ② CSV 文件

1. 同一页再勾 **「给 vMix 的 CSV」**
2. **追加方式**选「不追加」（只要最新一行）
3. 点 **「复制 CSV 路径」**
4. vMix → Data Sources → **+** → **Excel / CSV / Google Sheets** → 选 `results.csv`

`results.csv` 第一行是字段名（可用中文），第二行是当前值（另有一列时间戳）。

---

## ③ vMix API（SetText）

适合已经做好 GT 标题、想软件直接改标题文字的场合。

1. vMix → Settings → **Web Controller** → Enable（端口一般 **8088**）
2. 光为 OCR → 出站 → **vMix**
3. 主机/端口/Input 填好 → 映射表右列写标题字段名 → **启动**
4. 可点 **「一键复制 API 示例 / 当前映射」** 备忘（不改连接）

---

## 不要这样做

- 不要勾「每个字段单独 .txt」再往 vMix 加一堆 Text 源  
- 不要用 lone `{...}` 当 JSON（必须是 `[{...}]`）  
- 不要把 Web Controller 端口填成 8099（那是 TCP API，光为走 HTTP 8088）

HTTP / OBS 等仍在出站列表里，需要时再开，日常播比赛可以不理。
