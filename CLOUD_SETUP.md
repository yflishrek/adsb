# BTH ADS-B 云端采集部署指南（GitHub Actions 版）

## 结论：GitHub 能完全满足要求（前提是公有仓库）

你的电脑无法 24 小时开机，而 ADS-B 只能**实时采集**，所以"把本地数据同步到云端"没有意义——关机时根本没在采集。正确做法是把**采集本身放到云端**。

实测与官方额度对照：

| 项目 | 数值 | 来源 |
|------|------|------|
| 本机 → github.com | ✅ 可达（HTTP 200，0.9s；`git ls-remote` 正常） | 本机实测 |
| 公有仓库 Actions 分钟 | **免费、无限** | GitHub 官方计费文档 |
| 私有仓库 Free 配额 | 2,000 分钟/月 | 同上 |
| 单个 job 最长运行 | 6 小时 | GitHub 官方限制文档 |
| 本方案 14 天需求量 | 约 20,160 分钟 | 14×24×60 |

**结论：必须用公有仓库。** 私有仓库 2,000 分钟只够采约 33 小时（1.4 天），缺口 90%。

## 为什么用"长任务"而不是"每 10 分钟触发一次"

每 10 分钟触发 × 14 天 = 2,016 次构建。改为**单个 job 内部以 30 秒间隔循环 5 小时**，期间每 10 分钟提交一次：

- 构建次数：2,016 → **约 56 次**
- 采样分辨率：10 分钟 → **30 秒**（对航迹重建更有利）
- 单 job 5 小时 < 官方 6 小时上限，留 1 小时余量

## 部署步骤

### 1. 新建 GitHub 仓库（必须选 Public）

右上角 `+` → **New repository**

- Repository name：如 `bth-adsb`
- **Public**（关键：公有仓库才免费无限分钟）
- 初始化选项**全部不勾选**（不要 Add README / .gitignore / license），本地已有这些文件，勾选会冲突

点 **Create repository**。

### 2. 创建推送用的 Personal Access Token

GitHub 右上角头像 → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)** → **Generate new token (classic)**

- 勾选 scope：**`repo`**（完整仓库读写）
- 生成后**立刻复制**（只显示一次）

### 3. 本地推送

在 `D:/PARTD` 执行：

```bash
git remote remove origin                                  # 若已指向 Gitee
git remote add origin https://github.com/<用户名>/bth-adsb.git
git push -u origin master                                  # 若默认分支是 main 则改成 main
```

凭据提示时：用户名填 GitHub 用户名，密码填**上一步的 token**（不是登录密码）。

### 4. 配置 Secrets（密钥）

仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**，添加两个：

| Name | Secret |
|------|--------|
| `OPENSKY_CLIENT_ID` | `shrek-api-client` |
| `OPENSKY_CLIENT_SECRET` | `在 C:\Users\commi\Downloads\credentials.json 的 clientSecret 字段` |

> 注意：本文件不写明文密钥。仓库是 **Public**，任何写进文件的密钥都会对全网公开。
> 真实值只放在 GitHub Secrets 和本地 `credentials.json`（已被 .gitignore 排除）。

**不需要配推送密钥**——脚本会自动使用 Actions 内置的 `GITHUB_TOKEN`（工作流里已声明 `permissions: contents: write`）。

adsb.lol 无需密钥；即使不配 OpenSky，也会照常采集 adsb.lol 数据。

### 5. 验证

仓库 → **Actions** 标签页 → 左侧选 `bth-adsb-collect` → 右上角 **Run workflow** 手动跑一次。

日志里应看到类似：

```
adsb.lol: 28 aircraft -> data/flights/adsblol/adsblol_YYYYMMDD.csv
OpenSky: 27 aircraft -> data/flights/opensky/opensky_YYYYMMDD.csv
[tag] push rc=0
```

确认 `push rc=0` 后，定时触发（`0 */6 * * *`，UTC）会自动接管。

## 参数调整

`scripts/collect_longrun.py`：

```bash
python collect_longrun.py --interval 30      # 轮询间隔（秒）
                          --max-minutes 300  # 单任务最长运行（分钟，必须 < 6 小时）
                          --commit-every 10  # 每多少分钟提交一次
```

`--max-minutes` 必须小于 cron 间隔（6 小时 = 360 分钟），留 60 分钟余量防重叠。即使任务被中断，最多只丢最后不到 10 分钟的数据。

## 关于 Gitee 备份仓库

Gitee 私有仓库 `shrekfiona/adsb` 已作为镜像保留（配置在 `.workflow/bth_adsb_collect.yml`）。

但注意 **Gitee Go 免费额度仅约 500 分钟/月**（单仓库 200 分钟永久 + 个人 500 分钟/月），距 20,160 分钟差 97%，**无法用于本次 14 天连续采集**，仅作代码备份。

## 数据产出

- `data/flights/adsblol/adsblol_YYYYMMDD.csv` — 含机型、注册号、风速、温度
- `data/flights/opensky/opensky_YYYYMMDD.csv` — 含航迹、垂直速率
- 覆盖范围：以 (39.6, 116.8) 为中心 80 海里，涵盖 PEK / PKX / TSN 三机场

采集结束后合并（把机型补进航迹）：

```bash
python scripts/merge_sources.py
```

## 已知局限

1. **机型覆盖率**：靠 adsb.lol 快照按 ICAO24 累积，随天数上升但不会到 100%。无机型的航迹不能用于噪声建模，论文需写明剔除规则。
2. **负值高度**：`baro_altitude_m` 可能出现负值（OpenSky 气压基准伪迹），建模前需清洗。
3. **定时触发可能延迟**：GitHub 在负载高时会延后 cron 任务；仓库 60 天无活动会停用定时任务（本次连续采集不受影响）。
4. **仓库体积**：估算 14 天约 300–400 MB 数据提交。GitHub 单文件上限 100 MB，每日 CSV 约 12 MB，安全；但建议采集结束后再对仓库做一次容量检查。
