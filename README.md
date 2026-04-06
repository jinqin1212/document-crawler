# 中国裁判文书网检索与「腾讯电子签」研究流水线

本项目用于**合法授权的研究场景**：请遵守 [中国裁判文书网](https://wenshu.court.gov.cn/) 使用条款，**限速、礼貌爬取**；遇强反爬时以**人工补抓、仅保留列表**等方式降级，**禁止伪造裁判文书字段**。

---

## 目录结构（核心文件）

| 路径 | 说明 |
|------|------|
| `main.py` | 阶段 1–2：打开站点、检索、多页列表 → `data/wenshu_list_preview.json` |
| `wenshu_spider.py` | 爬虫逻辑（Playwright） |
| `wenshu_selectors.py` | **页面选择器集中配置**（改版后主要改这里） |
| `save_storage_state.py` | 一次性保存登录态 JSON |
| `fetch_details.py` | 阶段 3：按列表 JSON 抓取详情 HTML → `data/raw/` |
| `parser.py` | 阶段 4：HTML → 13 字段 + 预览表 |
| `export_cases.py` | 阶段 5：预览表 → `wenshu_cases.csv` / `.xlsx` |
| `analysis.py` | 阶段 6：读案例表 → `data/plots/` 图表与统计 CSV |
| `config.example.env` | 环境变量示例（复制为 `.env`） |
| `requirements.txt` | Python 依赖 |

数据目录：`data/raw/`（详情 HTML）、`data/plots/`（图表）、其余 CSV/JSON 见下文流水线。

---

## 环境要求

- Python 3.9+
- 推荐使用虚拟环境

```bash
cd /path/to/test01
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

---

## 配置 `.env`

```bash
cp config.example.env .env
```

按注释填写；**至少**建议配置：

- `STORAGE_STATE_PATH`：登录态文件路径（见下节）
- `HEADLESS=false`：开发/过验证时建议有界面
- `WENSHU_SIMPLE_SEARCH_ONLY`：是否仅用首页普通搜索（`true`）或高级检索（`false`）
- `MAX_PAGES`：列表最多翻页数

**勿将 `.env`、`data/wenshu_state.json` 提交到公开仓库**（`.gitignore` 已忽略常见项）。

---

## 登录态（`storage_state`）

1. 运行：`python save_storage_state.py`  
2. 在弹出的浏览器中完成登录/验证，回终端按 Enter。  
3. 在 `.env` 中设置：  
   `STORAGE_STATE_PATH=./data/wenshu_state.json`（路径以你保存为准）

`main.py` 与 `fetch_details.py` 会读取该文件以复用 Cookie/会话。

---

## 端到端流水线（推荐顺序）

在项目根目录、已 `source .venv/bin/activate` 且已配置 `.env`：

```bash
# 1) 列表（检索 + 翻页）
python main.py
# → data/wenshu_list_preview.json

# 2) 详情 HTML
python fetch_details.py
# → data/raw/*.html

# 3) 解析
python parser.py
# → data/wenshu_parsed_preview.csv / .json

# 4) 导出正式表
python export_cases.py
# → data/wenshu_cases.csv、data/wenshu_cases.xlsx

# 5) 图表
python analysis.py
# → data/plots/*.png、*.csv
```

列表未变时，可跳过 `main.py`，从中间某步继续。详情脚本支持 `DETAIL_SKIP_EXISTING=true` 跳过已下载的 HTML。

---

## 选择器失效时：用 Playwright codegen 更新

文书网 DOM/iframe 常变，**不要**在业务代码里散落魔法字符串；应改 **`wenshu_selectors.py`**（及必要时 `DETAIL_WAIT_SELECTORS`）。

```bash
playwright codegen https://wenshu.court.gov.cn/
```

在录制脚本中复制稳定定位方式（注意 **`frame_locator`** 若检索区在 iframe 内），合并进 `SELECTORS` 字典或 `DETAIL_WAIT_SELECTORS` 元组。

首页普通搜索按钮示例：`div.search-rightBtn.search-click`（已写入配置）。

---

## WAF / 验证码 / 顶栏「登录」

- 出现 **`waf_text_verify`** 时，脚本会等待你在浏览器内**手动完成验证**。  
- 自动点击顶栏「登录」可能触发 `open=login` 与 WAF；可通过 `WENSHU_CLICK_NAV_LOGIN=false` 关闭。  
- `HEADLESS=true` 不利于过验证码，生产环境若用无头需自行承担失败率。

---

## 降级思路（强反爬时）

- 仅导出列表 JSON/案号，**详情由人工**在浏览器中另存为 HTML 放入 `data/raw/`，再跑 `parser.py` 以后步骤。  
- 降低 `MAX_PAGES`、拉大 `DETAIL_DELAY_*`，避免封禁。  
- 解析规则无法 100% 准确时，以空字段 + `raw_标的片段`（可选导出列）保留复核依据，**不编造判决内容**。

---

## 阶段 4 解析说明（简要）

`parser.py` 从详情 HTML 抽取 13 个字段（案号、案由、标的、胜诉、腾讯电子签相关等），规则为**启发式**；复杂文书的标的额、胜诉判断可能偏差，需结合原文与 `raw_标的片段` 人工核对。字段定义见 `parser.py` 模块文档字符串。

---

## 图表与字体（`analysis.py`）

- 默认读 `data/wenshu_cases.csv`，输出到 `data/plots/`。  
- 中文字体依次尝试 PingFang SC、Arial Unicode MS、SimHei、Noto Sans CJK SC 等；若方框乱码，请在本机安装中文字体。  
- 使用非交互后端 **Agg**，并在项目下使用 `.mplconfig/` 作为 Matplotlib 配置目录（避免无写权限环境报错）。

自定义输入/输出：

```bash
python analysis.py data/wenshu_cases.csv -o data/plots
python analysis.py -h
```

---

## 安全与合规自检清单

- [ ] 仓库中**无**真实账号密码、无完整 `wenshu_state.json` 提交记录  
- [ ] `.env` 仅保留在本地  
- [ ] 对外分享数据前完成脱敏与授权确认  

---

## 常见问题

**Q：`main.py` 找不到 `#keyValue1`？**  
A：当前页可能是首页大搜索框；设 `WENSHU_SIMPLE_SEARCH_ONLY=true`，或进入高级检索后用 codegen 更新选择器/iframe。

**Q：`fetch_details.py` 很慢？**  
A：已优化为组合选择器短等待；仍慢多为站点或 WAF。可调 `DETAIL_BODY_TIMEOUT_MS`、`DETAIL_STABILIZE_S`。

**Q：`analysis.py` 报找不到 CSV？**  
A：默认路径为项目下的 `data/wenshu_cases.csv`；请先在项目根执行 `export_cases.py`，或传入实际文件路径（**不要使用**文档里的占位符 `path/to/...`）。

---

## 许可证与免责

代码仅供学习与研究参考；使用者对访问裁判文书网的行为及数据使用自行承担合规责任。

test
