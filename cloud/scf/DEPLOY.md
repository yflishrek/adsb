# 腾讯云 SCF —— 第二条采集通道（部署说明）

## 为什么要有第二条

GitHub Actions 的 `schedule` 在这个仓库上不可靠。2026-10-12 实测：配置为
`7,37 * * * *`（每 30 分钟，48 次/天）的 13.5 小时内只触发了 **3 次**，约 **89% 的
触发事件被延迟或丢弃**，最近 12.4 小时实际覆盖只有 **≈6%**。

问题**不在计算**（公有仓库 Actions 分钟数免费无限），只在**调度器**。所以让同一条
采集逻辑跑在第二个调度器上，最后取两条通道的并集。

| | 通道一 | 通道二（本文档） |
|---|---|---|
| 计算 | GitHub Actions | 腾讯云 SCF |
| 触发 | cron（实测丢 ~90%） | 定时触发器（每 15 分钟） |
| 单次时长 | 26 分钟 | 13 分钟 |
| 推送方式 | `git push` | GitHub **Data API** |
| 实测覆盖率 | ≈42%（近 48h） | 预期 ≈87% |

> **为什么不能用 `git push`**：SCF 运行时是只读容器，既没有 `git` 二进制，也没有
> `/tmp` 之外的可写文件系统。所以提交改走纯 HTTPS 的四个 API 调用
> （blob → tree → commit → update ref）。该路径已用一次性分支实测验证：内容逐字节
> 往返一致、`master` 不受影响。

---

## 1. 免费额度和成本（先算账）

| 项 | 数值 |
|---|---|
| 单次运行 | 128 MB × 13 min = 0.125 GB × 780 s = **97.5 GB·s** |
| 触发频率 | 每 15 分钟 → 96 次/天 → **2,880 次/月** |
| 月资源用量 | 2,880 × 97.5 = **约 28.1 万 GB·s** |
| 免费额度（新用户前 3 个月） | **100 万次调用 + 40 万 GB·s / 月** |
| 占用 | 资源约 **70%**、调用次数约 **0.3%** → **0 元** |

**两个必须知道的约束：**

1. **免费额度只覆盖前 3 个月**（含开通当月），需以 0 元购买「个人高级版套餐包」领取。
   本次采集窗口是 14 天，完全在窗口内；但**别长期挂着跑**。
2. **内存不要随意调大**。资源用量与内存成正比，改成 256 MB 就会直接翻倍到 ~56 万 GB·s
   **超出免费额度**。要么保持 128 MB，要么把 `SCF_MAX_MINUTES` 降到 7 分钟。
3. 超过免费额度后按量计费，14 天窗口内即使全部超量也只有几元级别，不是主要风险。

---

## 2. 打包

```bash
bash cloud/scf/build.sh
```

产出 `cloud/scf/scf.zip`（约 9.5 KB，3 个文件，**零第三方依赖**，不需要
`pip install`）。包里是扁平的三个文件：

```
index.py           SCF 入口（index.main_handler）
scf_collect.py     采集循环 + GitHub Data API 推送
cloud_collect.py   共用的取数与落盘层
```

`cloud_collect.py` 在没有 `requests` 时会自动回退到标准库 `urllib`，所以
**不需要给 SCF 打包任何依赖**，也不会改变 GitHub Actions 上的行为。

---

## 3. 控制台部署（推荐路径）

**Step 1 — 开通云函数**
控制台 → 云函数 SCF → 领取免费额度（0 元购买个人高级版套餐包）。

**Step 2 — 新建函数**

| 字段 | 值 |
|---|---|
| 创建方式 | 从头开始 |
| 函数名称 | `bth-adsb-scf-collector` |
| 运行环境 | `Python 3.9`（3.6+ 均可） |
| 提交方式 | **本地上传 zip 包** → 选 `cloud/scf/scf.zip` |
| 函数入口 | `index.main_handler` |
| 内存 | **128 MB** |
| 超时时间 | **900 秒** |

**Step 3 — 环境变量**（函数配置 → 环境变量）

| 变量 | 值 | 说明 |
|---|---|---|
| `OPENSKY_CLIENT_ID` | 你的 OpenSky client id | 与 GitHub Actions 同一套 |
| `OPENSKY_CLIENT_SECRET` | 你的 OpenSky secret | |
| `GH_TOKEN` | fine-grained PAT | **需带 `Contents: Read and write`** |
| `GH_REPO` | `yflishrek/adsb` | |
| `GH_BRANCH` | `master` | |
| `COLLECT_DATA_DIR` | `/tmp/data/flights` | SCF 只有 /tmp 可写 |
| `SCF_INTERVAL` | `30` | 轮询间隔（秒），决定航迹分辨率 |
| `SCF_MAX_MINUTES` | `13` | 单次窗口长度（分钟） |
| `SCF_COMMIT_EVERY` | `4` | 每 N 分钟推一次 |
| `GH_API_BASE`（可选） | `https://api.github.com` | 若 api.github.com 从该地域访问慢，可换成代理 |

**Step 4 — 定时触发器**（触发管理 → 新建触发器）

| 字段 | 值 |
|---|---|
| 触发类型 | 定时触发 |
| cron 表达式 | `0 */15 * * * * *` |
| 附加信息 | 留空 |

SCF 的 cron 是 **7 位**（秒 分 时 日 月 周 年），时区为 **UTC+8**。
`0 */15 * * * * *` = 每 15 分钟的 0 秒。

**Step 5 — 网络**
默认具备公网出口，无需 VPC / NAT。若你把函数放进 VPC，**必须配 NAT 网关**，否则
取不到数也推不了 Git。

---

## 4. 验证

1. 先把触发器临时设成 5 分钟后触发一次，或在控制台点「测试」手动跑一次。
2. 看运行日志，正常应看到：
   ```
   === SCF collector: dir=/tmp/data/flights interval=30s window=13.0min ===
     #1: adsb.lol=24 opensky=180
     pushed 2 file(s) -> 1a2b3c4d
   === SCF done: cycles=26 files_pushed=6 stats={...} ===
   ```
   关键三行：`adsb.lol=` 和 `opensky=` 都非 0（说明密钥生效），以及 `pushed N file(s)`。
3. 去 GitHub 仓库看提交，应出现带 **`[scf]`** 后缀的
   `chore(data): ADS-B snapshot ... [scf]`。
4. 本地核对（本仓库）：
   ```bash
   git pull                       # 把 SCF 那一路推上来的数据拉下来
   python scripts/check_freshness.py --max-age-min 40
   ```

**冒烟测试**（不部署也能跑，只采不发）：
```bash
cd scripts
COLLECT_DATA_DIR=/tmp/scf_test SCF_MAX_MINUTES=0.5 SCF_INTERVAL=6 \
  python scf_collect.py
```

---

## 5. 与 GitHub 那一路共存

- **文件名不会撞**：每条通道的文件名都带自己的通道标记
  （`adsblol_20261012_031834_scf.csv` / `..._gh....csv`）。批名保持
  `adsblol_<YYYYMMDD>*` 前缀不变，所以 `merge_sources.py` 的日期前缀匹配不受影响。
- **推送不会打架**：两条通道都只是**新增文件**，从不修改已有文件；对方的
  ref 变动只表现为一次 409/422，代码会重读 ref 并重试（最多 5 次，退避 2/4/6/8 秒）。
- **`master` 是唯一汇聚点**：两条通道都推到 `master`，所以下游分析不用改任何东西。

> ⚠️ **下游必须先做去重**。两条通道采的是**同一批物理航班**（都打 OpenSky
> `/states/all` 快照 + adsb.lol），不去重会让 Stage 1 的 SEL 累加把同一次飞越
> **重复计一次**，Lden 系统性偏高。去重已在 `scripts/merge_sources.py` 的
> `load_opensky()` 里实现（键：`(icao24, time_position)`）。

---

## 6. 排查

| 现象 | 原因 / 处理 |
|---|---|
| 日志有 `adsb.lol=0` 且 `opensky_skip` 在涨 | OpenSky 密钥没生效，检查环境变量 |
| `adsb.lol=0` 但 `opensky` 正常 | adsb.lol 上游覆盖临时缺失，会自动重试 3 次 |
| `!! push FAILED` | 检查 `GH_TOKEN` 是否有 `Contents: R/W`；或 `api.github.com` 从该地域不通，改用 `GH_API_BASE` |
| `!! GH_TOKEN / GH_REPO not set` | 环境变量漏配，数据写在 `/tmp` 里、函数退出即丢 |
| 函数被 900 秒硬杀、没看到 `=== SCF done` | 把 `SCF_MAX_MINUTES` 降到 11–12 |

---

## 7. CLI 部署（可选）

不想点控制台的话，`serverless.yml` 已备好（见同目录）：

```bash
bash cloud/scf/build.sh
export OPENSKY_CLIENT_ID=... OPENSKY_CLIENT_SECRET=... GH_TOKEN=...
sls deploy --inputs ./cloud/scf/serverless.yml
```

组件字段名在不同版本间有变动，**用之前请对照腾讯云当前的组件文档**；控制台路径
是更稳的选择。

> `serverless.yml` 里通过 `${env:...}` 读取密钥，**任何情况下都不要把 token 写进
> 文件**——本仓库是 public。
