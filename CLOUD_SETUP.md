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

- 构建次数：2,016 → **约 672 次**（30 分钟一班 × 14 天；公有仓库分钟免费，次数不是约束）
- 采样分辨率：10 分钟 → **30 秒**（由 job 内的轮询间隔决定，与 job 时长无关）
- 单 job 26 分钟 < 官方 6 小时上限：**故意留这么大余量，是因为 job 越短，单次失败/被延迟的代价越小**

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

确认 `push rc=0` 后，定时触发（`*/30 * * * *`，UTC，每 30 分钟一次）会自动接管。

> **为什么不再是 6 小时一次**（2026-10-11 实测后调整）：旧配置 `17 */6` + `--max-minutes 350`
> 的设计缺口每天仅 40 分钟，但实测 10-10 04:38Z ~ 10-11 08:08Z 的 27.5 小时里只有约 **55%**
> 时间在采集，缺口合计 **12.5 小时**（其中一次 7 小时 48 分）。两个原因：
> ① `#3` 任务被 runner 在 1h22m 处杀死，一次失败就丢掉 5h50m 预算里的 4.5 小时；
> ② GitHub 定时触发会延迟甚至丢弃——`18:17Z` 那一班实际 21:50Z 才起跑（+3h33m），直接造成 7h48m 空洞。
> 周期改短后，**任何一次失败或丢弃最多只损失 30 分钟**，且触发机会从 4 次/天增加到 48 次/天，
> 抖动被平均掉。分辨率不受影响：航迹分辨率由 job 内的 30 秒轮询决定，与 job 时长无关。

## 参数调整

`scripts/collect_longrun.py`：

```bash
python collect_longrun.py --interval 30      # 轮询间隔（秒）——决定航迹分辨率
                          --max-minutes 26   # 单任务最长运行（分钟，必须 < cron 间隔）
                          --commit-every 10  # 每多少分钟提交一次
```

`--max-minutes` 必须小于 cron 间隔（30 分钟），留 4 分钟给 checkout 与依赖安装。
**job 越短，单次失败/被延迟的代价越小**：26 分钟的任务即使中途被杀，也只丢不到半小时。
`workflow` 里另设 `timeout-minutes: 40` 兜底，防止卡死的 job 一直占着 concurrency 槽位。

`concurrency: {group: bth-adsb-collect, cancel-in-progress: false}` 保证同一时刻只有一个
采集器在跑：用 queue 而不是 cancel，避免两个进程同时写同一个 `DATA_BATCH` 文件。

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

## 每日体检

```bash
python scripts/daily_check.py --date 20261010          # 指定日期（本地数据）
python scripts/daily_check.py --date 20261010 --remote # 本地没有时从 GitHub 下载
python scripts/daily_check.py --all --csv              # 全部日期并输出汇总表
python scripts/merge_sources.py --date 20261010        # 刷新累积机型字典
```

体检脚本报告：快照轮次与完整率、最长数据缺口、唯一航空器数、机型覆盖率（累积字典 / 当日重合两个口径），并给出「采样 OK / 偏低 / 严重缺失」和「机型覆盖充足 / 可接受 / 不足」的判定。

判定标准：采样完整率 ≥95% 为 OK（30 秒间隔下每天应有 2880 轮）；机型覆盖率 ≥70% 充足，50–70% 可接受，<50% 需延长采集或调整数据来源表述。

> 注意：`--remote` 走本机代理，可能偶发失败，脚本内置三次重试；仍失败时改用 `git pull`。

## 停止采集的告警（`bth-adsb-health`）

GitHub 默认只在「某次运行失败」时发邮件，而那个信号**既会误报也会漏报**：

* 运行中途被 runner 杀死会发「run failed」——但那种情况下之前已经采到的数据是好的，容易误读成"采集停了"；
* 定时触发被**丢弃**时一封邮件都不发（什么都没跑，自然没有失败）——这是真正危险的漏报。

所以另建 `.github/workflows/health.yml`（每 30 分钟的第 7、37 分），它只问一个唯一重要的问题：
**最新数据有多旧？** 采集器运行时每 10 分钟提交一次数据，因此「最近一次改动 `data/flights/`
的提交距今多久」就是采集是否存活的直接度量；超过 40 分钟即退出非零 → GitHub 发出**可操作的**告警。

本地随时手查（`--ref` 可审计远端分支而不是本地检出）：

```bash
python scripts/check_freshness.py                       # HEAD，超过 40 分钟则退出 1
python scripts/check_freshness.py --ref origin/master    # 审计远端实际状态
python scripts/check_freshness.py --report               # 只打印，不改变退出码
```

另一个静默故障：**adsb.lol 会返回 HTTP 200 + `"ac": []`**（不是错误码），表示其馈线在该区域
没有覆盖。旧代码把它当成"当前无航班"，于是写出 219 字节的纯表头 CSV —— 2026-10-11 08:37–11:34
北京时连续 18 个窗口如此，留下一个**看起来像数据**的 3 小时空洞（同期 OpenSky 正常，所以按
"总行数>0"判断永远发现不了）。现在 `collect_adsblol()` 会在周期内重试 3 次、把该窗口计为
`adsblol_empty`、**且不再写文件**；`collect_longrun` 另外跟踪 adsb.lol 连续空窗并在 5/20/60/120
轮时打印醒目告警。回归测试：`python scripts/test_cloud_collect.py`。

## 能连续跑多久（可用性边界）

**结论：14 天完全没问题；约 2 个月内安全；跑满 1 年在技术上不可行（不是被封的概率问题，是仓库会撑爆）。**

### 官方硬性限制（actions/reference/limits）

| 限制项 | 官方数值 | 本方案 | 状态 |
|--------|---------|--------|------|
| 公有仓库 Actions 分钟 | 免费无限 | 约 1,300 分钟/天 | ✅ 不受限 |
| 单个 job 最长运行 | 6 小时 | 26 分钟（+40 分钟兜底超时） | ✅ |
| 单个 workflow run | 35 天 | 每次 26 分钟 | ✅ |
| 并发 job（Free） | 20 | 1（concurrency 串行化） | ✅ |
| 最短 cron 间隔 | 5 分钟 | 30 分钟 | ✅ |
| 单文件大小 | 硬限 100 MB | 日 CSV 约 23 MB | ✅ |
| **仓库体积** | 建议 <1 GB，**强烈建议 <5 GB** | **约 25 MB/天** | ⚠️ **唯一真瓶颈** |
| 仓库 60 天无活动 → 停用定时触发 | 是 | 每 10 分钟自动提交 | ✅ 天然免疫 |

### 存储实测与推算

实测（本地按真实节奏复刻一天：144 次提交，每次追加 20 个快照）：

| 指标 | 数值 |
|------|------|
| 当日 CSV 数据量 | 22.8 MB |
| git 打包后仓库增量 | **16.8 MB** |
| 若不压缩（各版本全量累加） | 约 1,644 MB |

git 的增量压缩对"追加写入的纯文本"效率极高，**仓库每天实际只涨约 17 MB**。据此：

| 采集时长 | 预计仓库体积 | 是否安全 |
|---------|------------|---------|
| 14 天 | 约 240 MB | ✅ 安全 |
| 30 天 | 约 510 MB | ✅ 安全 |
| 60 天 | 约 1.0 GB | ⚠️ 触及 1 GB 建议值 |
| 90 天 | 约 1.5 GB | ⚠️ 超出建议，可能收到 GitHub 邮件要求整改 |
| 365 天 | 约 6.2 GB | ❌ 超出 5 GB 强建议上限 |

### 服务条款风险（要如实知道）

GitHub 附加产品条款原文列出 Actions 禁止用途，其中最相关的一条：

> "If using GitHub-hosted runners, any other activity **unrelated to the production, testing, deployment, or publication of the software project associated with the repository** where GitHub Actions are used."
>
> 以及："any activity that places a burden on our servers, where **that burden is disproportionate to the benefits** provided to users"

**本方案的位置**：仓库本身就是一个数据采集项目，采集结果即该仓库软件的产出，因此**不是"与项目无关的活动"**，落在灰色地带偏安全的一侧。而且单任务只占 1 个 runner，负担很小。

**但风险不为零**：若长期 100% 占满 duty cycle，理论上可能收到用量提醒或任务被终止。降低风险的做法：

1. 只用这**一个**工作流，不铺开到多个仓库分摊（那才像滥用）
2. 仓库 README / About 里写明这是学术研究的数据采集管线，说明用途
3. 发现 GitHub 邮件提醒立即降采样（改 `--interval 60` 直接减半）

## 如果要采满一年：换存储，不改平台

按 17 MB/天计算，一年约 6.2 GB。三条可行路：

| 方案 | 成本 | 说明 |
|------|------|------|
| **数据外移到对象存储** | 免费额度通常够 | Cloudflare R2 / Backblaze B2 免费层约 10 GB；改采集器用 S3 接口直传，git 只存脚本不存数据 |
| **按月归档 + 清历史** | 免费 | 每月把该月 CSV 打成 Release 附件，再用 `git filter-repo` 清掉历史里的数据 |
| 轻量云服务器 | 约 ¥10–30/月 | 彻底没有仓库体积与条款顾虑，cron 完全可控 |

推荐优先做**对象存储直传**——它同时解决了体积和条款两个顾虑（CI 只做触发，不承担存储），且采集不需要 24 小时占用 runner 之外的资源。

## 已知局限

1. **机型覆盖率**：靠 adsb.lol 快照按 ICAO24 累积，随天数上升但不会到 100%。无机型的航迹不能用于噪声建模，论文需写明剔除规则。
2. **负值高度**：`baro_altitude_m` 可能出现负值（OpenSky 气压基准伪迹），建模前需清洗。
3. **定时触发会延迟甚至被丢弃**：GitHub 在负载高时会延后 cron 任务，严重时直接丢弃排队的任务。
   实测 10-10 的 `18:17Z` 那一班延迟了 **3 小时 33 分**才起跑。缓解手段是把周期从 6 小时缩到
   30 分钟（单次损失上限从 6 小时降到 30 分钟）并加 `bth-adsb-health` 告警；
   **但这只是降低概率，不能根除**——若 14 天窗口期间完整率达不到要求，见下文"轻量云服务器"。
   仓库 60 天无活动会停用定时任务（本次连续采集因持续提交天然免疫）。
4. **adsb.lol 会整段时间返回空**：见上一节。它影响的是**机型字段**（OpenSky 不提供 `t`），
   而无机型的航迹不能用于噪声建模，所以空窗会直接减少可用样本。
5. **仓库体积**：约 17 MB/天（55% 覆盖率下）。覆盖率提升到 ~87% 后估算约 25 MB/天 → 14 天约
   350 MB，安全；超过 60 天需按上文「如果要采满一年」处理。
