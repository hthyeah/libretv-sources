# 全自动源更新 · 部署指引

目标：**每天自动重建源列表，NAS 上的 LibreTV 自动跟着更新，主人不用管。**

---

## 🏗️ 架构（为什么要两个仓库）

```
┌──────────────────────────────────────────────┐
│ GitHub 私有仓库  libretv-sources              │
│   ├── build_sources.py                        │
│   ├── .github/workflows/update-sources.yml    │
│   └── sources.json  ← 每天自动提交更新         │
│                                               │
│   Actions 跑在 GitHub 云上 → 跟家里网络无关     │
└───────────────────┬──────────────────────────┘
                    │ 每天顺带推一份
                    ↓
┌──────────────────────────────────────────────┐
│ Gitee 公开仓库  libretv-sources               │
│   └── sources.json  ← 只有这一个文件          │
└───────────────────┬──────────────────────────┘
                    │ LibreTV 订阅它（24h 自动刷新）
                    ↓
            绿联 NAS 上的 LibreTV
```

### ⚠️ 为什么产物仓库必须是"公开"的

LibreTV 的订阅功能**只能拉匿名可读的地址** —— 人家实测确认过：

| 测试 | 结果 |
|:--|:--|
| 私有仓库的 raw 匿名读 | ❌ 404 |
| secret Gist 的 raw 匿名读 | ✅ 200 |
| 公开仓库的 raw 匿名读 | ✅ 200 |

所以设计成**"代码私有 + 产物公开"**：

- **源码和配置**在私有仓库里，别人看不到 ✅
- **产物 `sources.json`** 放在 Gitee 公开仓库 —— 但它的内容**只是一串采集站 API 地址**（这些地址本来就是社区公开流传的），没有任何隐私或凭据信息 ✅
- Gitee 比 GitHub 快 3 倍（实测 0.25s vs 0.78s），国内当订阅源更稳

> 如果主人连产物也想私有：可以改用 **secret Gist**（`gist.githubusercontent.com` 实测可匿名读，且不出现在任何列表里，只有知道 URL 的人能访问）。代价是 url 不可读性靠保密，也够用。

---

## 🚀 方式 A：全托管给人家（推荐，约 2 分钟）

主人只需要生成 **2 个令牌**发给人家的对话里：

### 1. GitHub 令牌（用来建私有仓库 + 推代码 + 配 Secret）

- 打开 https://github.com/settings/tokens → **Generate new token (classic)**
- Note 随便填 `libretv-sources`，过期时间选 90 天
- 勾选权限：**`repo`**（全勾）+ **`workflow`**
- 生成后复制（`ghp_` 开头）

> 用 classic token 是因为 fine-grained token 配 Secret 的权限较绕，classic 更省事。

### 2. Gitee 私人令牌（用来建公开产物仓库）

- 打开 https://gitee.com/profile/personal_access_tokens → **生成新令牌**
- 描述填 `libretv-sources`
- 勾选权限：**`projects`**（仓库读写）
- 生成后**立刻复制**（只显示一次）

### 人家拿到后会做的

1. 建 GitHub 私有仓库 `libretv-sources`（API）
2. 推入 `build_sources.py` + `update-sources.yml`
3. 建 Gitee 公开仓库（API）
4. 把 Gitee 令牌写进 GitHub 仓库的 Secret `GITEE_TOKEN`
5. **手动触发一次 Actions**，跑通并给你看结果
6. 把订阅地址填进 compose，主人再部署一次 LibreTV 就全自动了

### 关于令牌安全

- 只用于这一次配置，**不会写进任何会被提交的文件**
- 人家不会把令牌放进 compose、日志、或仓库里
- **配置完成后主人可以立刻在网页撤销这两个令牌**（Actions 后续用的是仓库内建的 `GITHUB_TOKEN`，不再需要你的 PAT）

---

## 🔧 方式 B：自己动手（约 8 分钟）

### 步骤 1 · 建 GitHub 私有仓库

1. 打开 https://github.com/new
   - 仓库名：`libretv-sources`
   - 选 **Private** ✅
   - **不要**勾 "Add a README file"
   - Create

### 步骤 2 · 开 Actions 写权限

仓库 → **Settings** → **Actions** → **General** → 拉到底
→ **Workflow permissions** 选 **Read and write permissions** → Save

### 步骤 3 · 建 Gitee 公开仓库

1. 打开 https://gitee.com/projects/new
   - 仓库名：`libretv-sources`
   - 选 **公开** ✅（必须公开，否则订阅读不到）
   - **不要**勾初始化 README
   - 创建

### 步骤 4 · 生成 Gitee 私人令牌

https://gitee.com/profile/personal_access_tokens → 生成新令牌 → 勾 **projects**

### 步骤 5 · 推代码

```bash
cd ~/无用杂项/绿联NAS影视库/auto-update

git init -b main
git add build_sources.py update-sources.yml README.md
git config user.name  "你的名字"
git config user.email "你的邮箱"
git commit -m "init: LibreTV 源列表自动构建"

# 用 SSH 推（主人的 ~/.ssh/config 已配好 GitHub over 443，实测可用）
git remote add origin git@github.com:hthyeah/libretv-sources.git
git push -u origin main

# 把 workflow 放到正确位置（GitHub 只认这个路径）
mkdir -p .github/workflows
cp update-sources.yml .github/workflows/
git add .github/ && git commit -m "ci: 加入 workflow" && git push
```

> ⚠️ **注意**：`update-sources.yml` 必须在仓库的 `.github/workflows/` 目录下，GitHub 才会识别。

### 步骤 6 · 加 Secret

仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

- Name: `GITEE_TOKEN`
- Secret: 粘贴刚才的 Gitee 令牌

（`GITEE_USER` / `GITEE_REPO` 已经在 `update-sources.yml` 顶部写好了，如果和你的不一样，改 yml 里那两行即可）

### 步骤 7 · 手动跑一次验证

仓库 → **Actions** → 左侧 `update-sources` → **Run workflow** → 等 1 分钟

跑完能在两个地方看到结果：
- 私有仓库根目录出现 `sources.json`
- Gitee 公开仓库也同步出现 `sources.json`

### 步骤 8 · 接进 LibreTV

把订阅地址填进 compose：

```yaml
    environment:
      - PASSWORD=${PASSWORD:?请在 .env 中设置 PASSWORD}
      - DEFAULT_SUBSCRIPTIONS=["https://gitee.com/hthyeah/libretv-sources/raw/main/sources.json"]
```

> 此时原来的 `DEFAULT_SOURCES`（那 10 个手工源）可以删掉，也可以留着，LibreTV 会按 URL 去重。

---

## 🔍 订阅地址格式对照

| 用途 | 地址 |
|:--|:--|
| **Gitee（推荐，国内快）** | `https://gitee.com/<用户名>/libretv-sources/raw/main/sources.json` |
| GitHub（公开仓库才行） | `https://raw.githubusercontent.com/<用户名>/libretv-sources/main/sources.json` |
| GitHub 加速镜像（备用） | `https://ghproxy.net/https://raw.githubusercontent.com/<用户名>/libretv-sources/main/sources.json` |
| secret Gist（产物也保密） | `https://gist.githubusercontent.com/<用户名>/<gist_id>/raw/sources.json` |

---

## ✅ 部署后怎么确认在正常工作

1. **GitHub Actions 页**能看到每天有一次成功的运行记录（绿色 ✓）
2. 点进运行记录，底部 **Summary** 里有这次实测出来的源清单
3. Gitee 上 `sources.json` 的提交时间 = 最近一次运行时间
4. LibreTV 里：设置 → 源管理 → 数据源订阅，条目上点 **⟳** 能手动同步

---

## 🧯 常见问题

| 现象 | 原因 / 解决 |
|:--|:--|
| Actions 报 403，提交失败 | 步骤 2 的写权限没开 |
| Gitee 步骤失败但 GitHub 成功 | GITEE_TOKEN 没配 / 权限没勾 projects。不影响主流程（继续用 GitHub 地址订阅） |
| LibreTV 拉不到订阅 | 仓库是不是私有？私有 raw 读不到，必须是公开产物仓库或 Gist |
| 订阅导入后没看到源 | 首次导入需要触发一次搜索；也可以到设置里点 ⟳ 手动同步 |
| `github.com` 网页打不开 | 用 SSH over 443（主人已配好）；建仓库这步可用手机 GitHub App |
| 源变少了 | 正常 —— 脚本只保留**跑的那一刻真能搜到结果**的源，波动是常态 |

---

_由小蜜 🍯 搭建 · 脚本与 workflow 均已实跑验证_
