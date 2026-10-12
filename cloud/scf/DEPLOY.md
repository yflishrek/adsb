# 腾讯云 SCF —— 唯一采集通道（部署说明）

## 为什么只留腾讯云

GitHub Actions 的 `schedule` 在这个仓库上不可靠。2026-10-12 实测：配置成
`7,37 * * * *`（48 次/天）的 13.5 小时内只触发了 **3 次**，约 **89% 的触发事件被延迟
或丢弃**，最近 12.4 小时实际覆盖只有 **≈6%**；当天晚些时候又断了 3 小时以上。

问题**不在计算**（公有仓库 Actions 分钟数免费无限），只在**调度器**。所以采集整体
迁到腾讯云 SCF，GitHub Actions 的采集任务停用（保留为手动应急开关）。

**数据也不再进 GitHub 仓库**，改存**腾讯云 COS 对象存储**。原因是 SCF 容器是临时的：
只有 `/tmp` 可写，且容器回收后即消失，采集结果必须落到容器之外。

| | 现在 | 之前 |
|---|---|---|
| 触发 | SCF 定时触发器（每 15 分钟） | GitHub cron（实测丢 ~90%） |
| 单次时长 | 13 分钟 | 26 分钟 |
| 存储 | **腾讯云 COS** | GitHub 仓库 |
| 依赖 git | 不需要 | 需要（SCF 上没有 git，得走 Data API） |

> **为什么自己实现 COS 签名**：部署包一直保持"零第三方依赖"，直接在控制台上传。
> 引入 `cos-python-sdk-v5` 会连带 `requests`、`crcmod` 等几百 KB。COS V5 签名本质是
> 对一段规范字符串做 HMAC-SHA1，`hashlib` + `hmac` 就够了。
> 规范串已与腾讯云官方文档的示例逐字节比对通过
> （`SHA1("get\n/1.jpg\n\n\n")` = `9434d3a0f2a5ecf18545baa37a838bf1e0132a85`）。

---

## 0. 你要做的三件事（总览）

1. 建一个 **COS 存储桶**（存数据）
2. 给函数配一个**运行角色**，让它有权写这个桶（这样就不用在控制台里存长期密钥）
3. 建/改 SCF 函数：传新 zip、填环境变量、加定时触发器

---

## 1. 免费额度先算账

| 项 | 数值 |
|---|---|
| 单次运行 | 128 MB × 13 min = 0.125 GB × 780 s = **97.5 GB·s** |
| 触发频率 | 每 15 分钟 → 96 次/天 → **2,880 次/月** |
| 月资源用量 | 2,880 × 97.5 = **约 28.1 万 GB·s** |
| 免费额度（新用户前 3 个月/月） | **100 万次调用 + 40 万 GB·s** |
| 占用 | 资源约 **70%**、调用次数约 **0.3%** → **0 元** |

COS 侧：14 天语料约 350–400 MB，标准存储免费额度（新用户 6 个月 50 GB）绰绰有余。

**两个约束：**

1. **SCF 免费额度只覆盖前 3 个月**（含开通当月），需 0 元购买「个人高级版套餐包」。
   14 天采集窗口完全在窗口内，但**别长期挂着**。
2. **内存不要调大**：资源用量与内存成正比，改成 256 MB 会翻倍到 ~56 万 GB·s
   **超额度**。要调就同时把 `COLLECT_MAX_MINUTES` 降到 7。

---

## 2. 打包

```bash
bash cloud/scf/build.sh
```

产出 `cloud/scf/scf.zip`（约 15 KB，**4 个文件，零第三方依赖**，无需 `pip install`）：

```
index.py           SCF 入口（index.main_handler）
scf_collect.py     采集循环 + 存储后端分派
cloud_collect.py   共用的取数与落盘层
cos_client.py      COS 上传（纯标准库 V5 签名）
```

---

## 3. 建 COS 桶并授权

**Step 1 — 建桶**

COS 控制台 → 存储桶列表 → 创建存储桶：

| 字段 | 值 |
|---|---|
| 名称 | 例如 `bth-adsb`（控制台会自动加 `-APPID` 后缀，**最终名字要带后缀**，如 `bth-adsb-1300000000`） |
| 地域 | 与 SCF 函数**同一地域**（如 `ap-beijing`），跨地域会走公网且更慢 |
| 访问权限 | **私有读写**（默认，保持即可） |

**Step 2 — 建运行角色（关键一步，替代长期密钥）**

SCF 函数可以绑定一个「运行角色」。绑定后腾讯云会自动往容器里注入**短期**凭据
环境变量 `TENCENTCLOUD_SECRETID` / `TENCENTCLOUD_SECRETKEY` /
`TENCENTCLOUD_SESSIONTOKEN`，代码直接读它们即可写桶。

> 这三个变量名之所以是 `TENCENTCLOUD_` 开头（也是控制台**不允许人工设置**的前缀），
> 正是因为它们由平台注入。所以你的 SecretId/SecretKey **不需要写进任何地方**。

在函数配置页把「运行角色」打开 → 新建角色 → 给它绑定写桶权限，例如
`QcloudCOSDataFullControl`（最省事）；想更收紧就用自定义策略只放行
`cos:PutObject` + 目标桶的 `data/flights/*`。

**Step 3 — 记录桶信息**

后面要填两个环境变量：桶名（**含 `-APPID` 后缀**）和地域。

---

## 4. 部署函数

**Step 1 — 新建函数**

| 字段 | 值 |
|---|---|
| 创建方式 | 从头开始 |
| 函数类型 | **事件函数**（⚠️ 不是 Web 函数） |
| 函数名称 | `bth-adsb-scf-collector` |
| 运行环境 | `Python 3.9`（3.6+ 均可） |
| 提交方式 | **本地上传 zip 包** → 选 `cloud/scf/scf.zip` |
| 函数入口 | `index.main_handler` |
| 内存 | **128 MB** |
| 执行超时 | **900 秒**（⚠️ 默认只有 3 秒，必须改） |
| 初始化超时 | 30 秒即可 |
| 临时存储 | 0.5 GB（默认） |
| 运行角色 | **启用**（见 Step 2） |

**Step 2 — 环境变量**（函数配置 → 环境变量 → **导入**）

一整段粘进去即可（每行 `key=value`）：

```
OPENSKY_CLIENT_ID=你的OpenSky客户端ID
OPENSKY_CLIENT_SECRET=你的OpenSky密钥
COS_BUCKET=bth-adsb-1300000000
COS_REGION=ap-beijing
COLLECT_DATA_DIR=/tmp/data/flights
COLLECT_INTERVAL=30
COLLECT_MAX_MINUTES=13
COLLECT_COMMIT_EVERY=4
```

| 变量 | 说明 |
|---|---|
| `OPENSKY_CLIENT_ID` / `OPENSKY_CLIENT_SECRET` | OpenSky OAuth2 凭据，**必填**，否则只剩 adsb.lol 一路 |
| `COS_BUCKET` | 桶名，**含 `-APPID` 后缀** |
| `COS_REGION` | 如 `ap-beijing`，与桶一致 |
| `COLLECT_DATA_DIR` | `/tmp/data/flights`，SCF 只有 `/tmp` 可写 |
| `COLLECT_INTERVAL` | 轮询间隔（秒），决定航迹分辨率 |
| `COLLECT_MAX_MINUTES` | 单次窗口长度（分钟） |
| `COLLECT_COMMIT_EVERY` | 每 N 分钟上传一次 |
| `COLLECT_STORAGE` | 可选。`cos` / `github` / `none`；**留空自动判定**（配了桶走 COS） |

> ⚠️ **不要用 `SCF_` 开头的变量名。** 腾讯云把 `SCF_`、`QCLOUD_`、`TENCENTCLOUD_`
> 列为平台预留 key，**控制台会直接拒绝保存**，而且不报错、函数静默跑在默认值上。
> 所以调优变量统一叫 `COLLECT_*`。代码仍兼容旧拼写（非腾讯云环境用）。

> ⚠️ **不要填 `GH_TOKEN` / `GH_REPO`。** 那是旧的 GitHub 后端，现已弃用；一旦填了且
> 没配 `COS_BUCKET`，自动判定会选中 GitHub 那条路。

**Step 3 — 定时触发器**（触发管理 → 新建触发器）

| 字段 | 值 |
|---|---|
| 触发类型 | 定时触发 |
| cron 表达式 | `0 */15 * * * * *` |
| 附加信息 | 留空 |

SCF 的 cron 是 **7 位**（秒 分 时 日 月 周 年），时区 **UTC+8**。
`0 */15 * * * * *` = 每 15 分钟的 0 秒。

**Step 4 — 网络**

默认具备公网出口，无需 VPC / NAT。若你把函数放进 VPC，**必须配 NAT 网关**，否则
取不到数也传不了 COS。

---

## 5. 验证

1. 「函数代码」页点**测试**手动跑一次。
2. 日志正常应看到：

   ```
   === SCF collector: dir=/tmp/data/flights interval=30s window=13.0min push-every=4.0min storage=cos ===
   storage: COS bucket=bth-adsb-1300000000 region=ap-beijing (credentials from SCF execution role)
     #1: adsb.lol=24 opensky=180
     pushed 2 file(s) via cos -> 2 object(s) -> bth-adsb-1300000000
   === SCF done: cycles=26 files_pushed=6 storage=cos stats={...} ===
   ```

   **三处必看**：
   - 第一行 `storage=cos` —— 不是 `none`（否则数据只写 /tmp，函数退出即丢）
   - `(credentials from SCF execution role)` —— 说明运行角色生效
   - `adsb.lol=` 与 `opensky=` —— OpenSky 那个**允许为 0**（见下），但 adsb.lol 应为非 0

3. 去 COS 控制台「文件列表」看，应出现 `data/flights/opensky/…_scf.csv` 与
   `data/flights/adsblol/…_scf.csv`。

> **OpenSky 为 0 不一定是配错**。2026-10-12 实测 OpenSky 对北京片区的供数会整段中断
> （BTH 框连续探测都是 0，而同一时刻 adsb.lol 同框仍有 12 架、中国其他区域仍有 206 架）。
> 这正是"按窗口选主源"存在的原因——只要 adsb.lol 非 0，这一窗就有数据。

**本地取数**（数据现在不在 git 里，必须用同步脚本）：

```bash
export COS_BUCKET=... COS_REGION=... COS_SECRET_ID=... COS_SECRET_KEY=...
python scripts/sync_cos.py --pull        # 增量拉到 data/flights/
python scripts/sync_cos.py --list        # 桶内清单
python scripts/merge_sources.py --date 20261011
```

> `scripts/sync_cos.py` 需要一对**长期密钥**（`COS_SECRET_ID`/`COS_SECRET_KEY`），
> 因为运行角色机制只存在于腾讯云内部。建议建一个**只读**子用户来跑它。

**冒烟测试**（不部署也能跑，只采不上传）：

```bash
cd scripts
COLLECT_DATA_DIR=/tmp/scf_test COLLECT_STORAGE=none \
COLLECT_MAX_MINUTES=0.5 COLLECT_INTERVAL=6 python scf_collect.py
```

---

## 6. 迁移历史数据（从 GitHub 时期迁到 COS）

仓库里 2026-10-09 到 10-12 的语料是 GitHub 通道采的。既然数据改为只存腾讯云，
把这批历史灌进 COS，让桶成为唯一权威源：

```bash
python scripts/sync_cos.py --upload-history --dry-run   # 先看要传什么
python scripts/sync_cos.py --upload-history             # 只传桶里没有的
```

该命令**只上传缺失对象**，可重复执行。

**停用 GitHub 采集通道**：`.github/workflows/collect.yml` 与 `health.yml` 的 cron
已移除，仅保留 `workflow_dispatch`（手动应急开关），不会再自动写仓库。
需要彻底删除时直接删这两个文件即可。

---

## 7. 排查

| 现象 | 原因 / 处理 |
|---|---|
| 日志 `storage: none` + `NO storage backend configured` | `COS_BUCKET`/`COS_REGION` 没配，或运行角色没启用。**数据会随容器销毁丢失** |
| `HTTP 403 on PUT` | 运行角色缺 COS 写权限，或被桶策略拒绝；确认角色已绑 `PutObject` |
| `HTTP 404 on PUT` | `COS_BUCKET` 写错（漏了 `-APPID` 后缀）或 `COS_REGION` 与桶不一致 |
| 日志 `adsb.lol=0` 但 `opensky` 正常 | adsb.lol 上游覆盖临时缺失，自动重试 3 次；持续则看第 5 节说明 |
| 日志 `opensky_skip` 在涨 | OpenSky 凭据没配或失效 |
| `!! push FAILED via cos, keeping files in /tmp` | 上传失败，文件**故意保留**在 /tmp，下一窗口重试。属自愈行为，但连续出现要查权限 |
| 函数被 900 秒硬杀、没有 `=== SCF done` | 把 `COLLECT_MAX_MINUTES` 降到 11–12 |
| 控制台拒绝保存某个变量 | key 用了 `SCF_`/`QCLOUD_`/`TENCENTCLOUD_` 预留前缀，改叫 `COLLECT_*` |
| SCF 上 `import requests` 报错 | 不需要 `requests`，`cloud_collect.py` 会自动回退 `urllib`；报了说明环境异常 |

---

## 8. CLI 部署（可选）

`serverless.yml` 已备好（见同目录）：

```bash
bash cloud/scf/build.sh
sls deploy --inputs ./cloud/scf/serverless.yml
```

组件字段名在不同版本间有变动，**用之前对照腾讯云当前组件文档**；控制台路径更稳。

> `serverless.yml` 通过 `${env:...}` 读密钥，**任何情况下都不要把密钥写进文件** ——
> 数据虽然改存 COS 了，但本仓库仍是 public。
