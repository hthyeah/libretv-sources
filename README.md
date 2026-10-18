# LibreTV 源列表自动更新（GitHub Actions 方案）

> 由小蜜 🍯 搭建 · 已本机实跑验证（5.6 秒跑完 50 个候选源，产出 12 个可用源）

---

## 为什么需要这个

LibreTV **自己有"自动侦测"**（源挂了自动熔断、恢复了自动启用），但**源列表本身不会自动换新** ——
社区会不断冒出新源、老源会换域名。这个方案让源列表**每天自动重建**：

```
GitHub Actions（每天 02:00）
   → 拉社区最新源目录
   → 逐个实测「能不能搜到结果」（不是"接口活着"）
   → 过滤成人源
   → 生成 sources.json 并提交
        ↓
LibreTV 订阅这个 JSON → 每 24h 静默刷新 → 源列表自动更新
```

---

## ⚠️ 一个关键约束（人家从源码确认的，别走弯路）

LibreTV 的订阅地址**必须是公网地址**。源码 `src/lib/ssrf.ts`：

```js
if (v.startsWith('192.168.')) return true;          // 判定为内网 → 拒绝
if (v.startsWith('10.'))      return true;
if (/^172\.(1[6-9]|2\d|3[01])\./.test(v)) return true;
const BLOCKED_HOSTNAMES = new Set(['localhost','127.0.0.1','0.0.0.0','::1']);
```

**所以不能在 NAS 上自建托管** —— `http://192.168.3.33:xxxx/sources.json` 会被 SSRF 校验拦掉。
必须放在公网可达的地方，**GitHub 是最省事的免费选择**（Actions 免费额度对每天跑一次绰绰有余）。

---

## 部署步骤（约 5 分钟）

### 1. 建一个仓库

GitHub 上新建一个仓库（私有也行），名字随便，例如 `libretv-sources`。

> 建议**私有**：源列表虽然不含敏感信息，但没必要公开。

### 2. 放两个文件

把本目录的两个文件按这个结构上传（**目录结构不能错**）：

```
你的仓库/
├── build_sources.py
└── .github/
    └── workflows/
        └── update-sources.yml
```

### 3. 开写权限

仓库 → **Settings** → **Actions** → **General** → 拉到最下 **Workflow permissions**
→ 选 **Read and write permissions** → Save

> 不开这个，Actions 跑完没法把 `sources.json` 提交回去（会报 403）。

### 4. 手动跑一次

仓库 → **Actions** → 左侧选 `update-sources` → 右侧 **Run workflow** → 等 1 分钟。

跑完仓库根目录会出现 `sources.json`，点开能看到这次实测挑选出的源。

### 5. 拿到订阅地址

```
https://raw.githubusercontent.com/<你的用户名>/<仓库名>/main/sources.json
```

> 📌 **国内访问备注**：`raw.githubusercontent.com` 时通时不通。如果 LibreTV 拉不到，换成
> jsDelivr CDN 地址（国内可达性好）：
> ```
> https://cdn.jsdelivr.net/gh/<你的用户名>/<仓库名>@main/sources.json
> ```
> jsDelivr 有缓存，源列表更新后可能要等一会儿才生效，属正常。

### 6. 接进 LibreTV

改 `docker-compose.yml`，加一行环境变量：

```yaml
    environment:
      - PASSWORD=${PASSWORD:?请在 .env 中设置 PASSWORD}
      - DEFAULT_SUBSCRIPTIONS=["https://raw.githubusercontent.com/你的用户名/你的仓库/main/sources.json"]
```

重新部署（`docker compose up -d`）。此后：

- **首次访问**：自动导入订阅里的源
- **超 24 小时**：在启动阶段**静默刷新**一次（不用你管）
- **拉取失败**：保留上次的数据，下次启动自动重试
- 想看效果：设置 → 源管理 → 数据源订阅，条目上有 **⟳** 可以随时手动同步

> 💡 用了订阅后，原来的 `DEFAULT_SOURCES` 可以删掉（避免和订阅重复）。保留也行，LibreTV 会按 URL 自动去重。

---

## 常用操作

| 想做什么 | 怎么做 |
|:--|:--|
| 立即更新源列表 | Actions → Run workflow |
| 看这次挑了哪些源 | 打开仓库里的 `sources.json` |
| 改筛选标准 | 改 `build_sources.py` 里的 `KEY`（探活关键词）、`TIMEOUT`、`ADULT_WORDS` |
| 加自己的私有源 | 改 `build_sources.py` 里的 `FALLBACK` 列表 |
| 改执行时间 | 改 `.yml` 里的 `cron`（注意是 **UTC** 时间） |

---

## 这个脚本跟"普通源列表"的区别

| | 普通社区源列表 | 本脚本产物 |
|:--|:--|:--|
| 判定标准 | 接口能连通 | **真能搜到结果** |
| 「接口活着但关了搜索」 | ❌ 算可用（误判） | ✅ 单独判定并剔除 |
| 成人源 | 常见 | 按名称 + **域名**双重过滤 |
| 重复条目 | 常见 | 按 API 地址去重 |
| 更新频率 | 看维护者 | 每天自动 |

实测对比：社区面板标「100% 在线」的 19 个源，能被本脚本选中的只有 **10–12 个** ——
差的那些就是「接口活着但搜索被关了」或「域名已迁移」。

---

## 免责声明

- 本脚本只读取第三方公开接口的元数据，**不下载、不存储任何视频内容**
- 源列表会随时间漂移，脚本只保证"跑的那一刻是准的"
- 请自行遵守当地法律法规，**不要把实例或源列表公开传播**

---

_由小蜜 🍯 整理 · 脚本已实跑验证_
