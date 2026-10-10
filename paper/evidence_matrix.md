# 证据矩阵 — Literature Review 底稿

> 用途：Literature Review 每一条论述的出处与核查状态。
> 核查标记：**[V]** = 已比对全文 PDF 提取文本，数字有原文支撑；**[T]** = 仅有题录信息，只做主题定位，文中不引具体数值；**[!]** = 待核验/需修正。
> 全文来源：`output_txt/*.txt`（8 篇，DOI 已与 `references.bib` 逐一匹配）。

---

## 一、有全文文献（8 篇，可引数值）

### L1 · pretto2024aircraft **[V]**
Pretto, M. et al. (2024). *Aircraft operation reconstruction and airport noise prediction from high-resolution flight tracking data*. TR-D 135, 104397. DOI 10.1016/j.trd.2024.104397 → `output_txt/1-s2.0-S1361920924003547-main.txt`

- **数据/方法**：高分辨率飞行追踪数据（high-resolution flight tracking data）；从爬升率剖面反推起飞重量（TOW）；重构地面航迹与飞行程序
- **场景**：Zurich Airport，2022 年交通量
- **关键结论**：能捕捉实际进离场程序，最终导致机场区域**基于暴露的累积噪声低估 1.7 dB(A)（平均）**
- **意义**：摆脱对航司专有运行数据的依赖
- **[!] 修正点**：原文标题与正文均使用 "high-resolution"，**未出现 "1-second resolution" 的表述**。论文初稿中 "ADS-B data at 1-second resolution" 属无出处细节，须改为 "high-resolution" 或补注原始数据源的时间分辨率。

### L2 · zhu2025dynamic **[V]**
Zhu, H. et al. (2025). *Dynamic assessment of airport noise impact*. TR-D 143, 104741. DOI 10.1016/j.trd.2025.104741 → `output_txt/1-s2.0-S1361920925001518-main.txt`

- **场景**：深圳宝安机场；ECAC Doc 29 应用于中国机场
- **精度**：与实测比对，**平均绝对误差 进港 1.75 dB / 离港 2.27 dB / 总体 2.07 dB**，满足中国生态环境部 3 dB 误差要求
- **动态暴露**：使用**百度慧眼**人口定位数据；**凌晨 2:00 差异最大**——动态比例较静态比例**相对高出约 30%**，对应绝对差 **1.88 个百分点**
- **[!] 修正点**：初稿 "deviate from static estimates by up to 30%" 未说明是**相对偏差**；30% 是相对量，绝对差仅 1.88 个百分点，表述不区分会被误读为绝对差异。

### L3 · xu2025flight **[V]**
Xu, Y., Kang, M., Chen, D., et al. (2025). *Flight departure optimization reduces airport CO2 emissions proved by Beijing-Tianjin-Hebei multiple-airport-region*. TR-D 143, 104920.（东南大学等）

- **方法**：数字孪生（digital twin）航班调度优化，以机场场面运行排放最小为目标
- **场景**：京津冀多机场区域（BTH MAR）
- **关键结论**：首都机场平均减少航班延误 **89 s**、减少起飞拥堵 **46 s**，起飞阶段 **CO₂ 减排 10%**
- **意义**：本研究最直接前驱——证明 MAR 协同范式可用于环境管理，但**只做了排放，未做噪声**

### L4 · suau2025does **[V]**
Suau et al. (2025). *Does cutting airport slots reduce climate impact? The case of Amsterdam airport*. TR-D, 104744.

- **场景**：阿姆斯特丹史基浦，荷兰政府将时刻从 **500,000 削减至 440,000**
- **关键结论**：短期减排幅度有限，且**不与削减规模成比例**，除非大幅削减长途航班；**机型升级（up-gauging）在航司行为未受约束时反而可能增加排放**
- **意义**：政策干预存在非直觉后果 → 对 MAR 时刻协同的警示

### L5 · han2025high **[T/V]**
Han, Z., Zhang, H., et al. (2025). *A high-resolution 3D emissions inventory of airport and its hotspot detection: A case study of Beijing Capital International Airport*. JCLP 512, 145698.

- **场景**：**北京首都国际机场（PEK）**——与本研究区域直接重叠
- **内容**：高分辨率三维排放清单 + 热点识别
- **用途**：支撑"机场环境影响具有高空间异质性、需精细刻画"的论点；但**是排放不是噪声**

### L6 · hu2020spatial **[T/V]**
Hu, R., Zhu, J., Zhang, Y., et al. (2020). *Spatial characteristics of aircraft CO₂ emissions at different airports: Some evidence from China*. TR-D 85, 102435.

- **内容**：**中国不同机场**航空 CO₂ 排放的空间特征
- **用途**：支撑"多机场之间环境影响存在结构性差异"→ MAR 层面研究的必要性

### L7 · heyes2021case **[T/V]**
Heyes, G., Hooper, P., Raje, F., Sheppard, J. (2021). *The case for a design-led, end-user focused airport noise management process*. TR-D 95, 102847.（曼彻斯特都会大学）

- **内容**：倡导以设计为主导、以终端用户（居民）为中心的噪声管理流程
- **用途**：支撑"噪声管理不能只停留在物理等值线，须纳入受影响人群"→ 衔接 Stage 3/4

### L8 · wu2021evaluating **[T]**
Wu et al. (2021). *Evaluating the economic and ecological impact for small and medium airport subsidies*. JCLP 281, 124811.

- **用途**：机场政策的经济—生态双重效应评估，支撑多机场差异化治理的必要性

---

## 二、仅题录文献（22 篇）— 只做主题定位，不引数值 **[T]**

### 噪声模型与标准
| key | 用途 |
|---|---|
| `ecac2016ceac` | ECAC Doc 29：噪声等值线计算国际标准框架（Approach A/B/C 分段法） |
| `icao2018recommended` | ICAO 推荐计算方法，与 Doc 29 互为参照 |
| `zaporozhets2016aircraft` | 机场噪声模型综述与改进 |
| `meister2021comparison` | sonAIR / FLULA2 / AEDT 三模型比对 → 说明模型选择带来系统性差异 |
| `simons2022comparative` | 阿姆斯特丹实测 vs  modelled 比对 → 验证难度 |
| `giladi2024validating` | AEDT 在希思罗的验证 |
| `wunderli2022aircraft` | 掠射入射条件下的噪声 → 传播物理细化 |
| `simons2026contribution` | 不同机型对 NOMOS 网络噪声的贡献 → 支撑"机型构成"变量 |
| `feng2024physics` | 物理驱动的 PSO-BPNN 混合模型 → 数据—物理融合趋势 |
| `einicke2025predicting` | 面向 2040 的噪声预测（交通增长 + 技术更新）→ 长期情景 |

### 监测与感知
| key | 用途 |
|---|---|
| `liu2025iot` | IoT 噪声感知与监测系统 → 实测数据可得性提升 |
| `zhao2022review` | 国内外机场噪声监控系统应用述评（中文） |
| `qu2024background` | 噪声监测中背景噪声影响及方法（中文）→ 实测数据质量控制 |
| `hong2023nonuniform` | 非均匀大气对远场声传播的影响（中文）→ 传播模型细化 |
| `zhuang2025four` | 离场程序四维航迹仿真及噪声影响（中文）→ 程序设计的噪声后果 |

### 多机场与区域
| key | 用途 |
|---|---|
| `deneufville1995management` | 多机场系统管理的奠基性工作 → MAR 概念源头 |
| `oconnor2015airports` | 机场与区域航空运输市场 → MAR 的空间经济维度 |

### 环境正义与政策
| key | 用途 |
|---|---|
| `audrin2022aircraft` | 蒙特利尔机场噪声与环境公平的空间分析 → 机场噪声正义的直接证据 |
| `fujiwara2017experience` | 机场及周边主观幸福感（ESM，Mappiness）→ 噪声的福利后果 |
| `icao2016balanced` | ICAO 平衡做法（Balanced Approach）→ 政策框架 |
| `wang2024noise` | 机场周边噪声控制国际比较与经验借鉴（中文）→ 政策对标 |

---

## 三、待办与风险

> 2026-10-10 更新：第 1、2、4、5 项已处理完毕（详见第四节）。

1. ~~**[P1]** 修正 Pretto 的 "1-second resolution" 表述~~ → **已改**为 "high-resolution"
2. ~~**[P1]** 修正 Zhu 的 30% 表述~~ → **已改**，明确区分相对偏差（~30%）与绝对差（1.88 个百分点），并加注"两者支撑不同论断"
3. **[P2]** `output_txt/1-s2.0-S136192091530256X-main.txt` 未能与 bib 匹配（9 篇中唯一），需确认是否遗漏条目
4. ~~**[P2]** 6 条中文文献缺 `year`~~ → **已补全**卷、期、页、年
5. ~~**[P2]** 作者写作 "and {others}"~~ → **已用 Crossref 补全全部 24 条英文文献的真实作者名单**

---

## 四、参考文献元数据核验（Crossref，2026-10-10）

用 `api.crossref.org` 逐条比对全部 DOI，发现并修正 **3 处硬错误**：

| 条目 | 原记录 | Crossref 实际 | 处理 |
|---|---|---|---|
| `fujiwara2017experience` | DOI `10.1016/j.trd.2016.09.011` | 该 DOI 指向另一篇文章（*The cost of convenience*，洛杉矶轻轨站，TRD 49:127-137） | **DOI 错误**，改为 `10.1016/j.trd.2017.07.015`；作者实为 Fujiwara, Lawton & MacKerron 三人（此前误写 5 人） |
| `deneufville1995management` | DOI `10.1016/0969-6997(95)00021-6` | 该 DOI 在 Crossref 返回 404，无法解析 | **DOI 错误**，改为 `10.1016/0969-6997(95)00035-6` |
| `oconnor2015airports` | 页码 78--89，年份 2015 | vol 53, pp. **78--82**, **2016** | 页码与年份均已修正；key 更名为 `oconnor2016airports`（同步更新正文引用） |

其余修正：
- `meister2021comparison` 原漏列第 4 作者 **Jäger, D.**（作者应为 6 人）
- `wunderli2022aircraft` 期刊名补全为 *The Journal of the Acoustical Society of America*
- `feng2024physics` 作者原为 "Feng, Z."，实为 **Feng, Hao**（名与缩写混淆）
- `wu2021evaluating` 作者 Qi 实为 **Hongbin Qi**，非 "Y."
- 期刊名统一去掉 `The` 前缀不一致；中文条目增加 `language={Chinese}` 字段（Elsevier 模板需要）
- 6 条中文期刊（CNKI 体系）未注册 Crossref，标 `[VERIFY]`，投稿前须人工核对

**Fujiwara 数字来源**：66 dB 关联性、百万级 ESM 样本、终端内非工作人群 happiness 上升 / relaxation 下降——这些数字**仍未经原文核验**（本地无该 PDF 全文），提交前必须比对 *Transportation Research Part D* 56:43-54 原文。


---

## 五、第二轮核验（2026-10-10 晚）——综述事实审计

用户要求"国内研究进展不应是文献堆叠"，本轮对 §2 逐句审计，把**每条断言回溯到原文**。发现并修正如下。

### 5.1 两处硬性事实错误（P0）

| 位置 | 原表述 | 原文实情 | 处理 |
|---|---|---|---|
| §2.1 Pretto | "applied to a **full year** of Zurich traffic" | 论文明确说明不可能处理全年（"would demand significant computational capabilities"），实际**只取 23 个代表日**，覆盖四季、日均交通覆盖率 **96.7%** | 已改为 23 代表日 + 96.7% |
| §2.1 Pretto | "take-off weights inferred from **climb-rate profiles**" | 原文："take-off weight is **estimated from the tracking data**"，方法是 mixed analysis–synthesis approach（增加飞行程序的自由度），**从未提到用爬升率反推** | 已改为 "estimated from the trajectories themselves" |

> 注：「爬升率反推 TOW」这一说法来自论文初稿，本轮核实为**无出处**，非本轮新引入。

### 5.2 补全的关键事实（增强而非削弱论证）

- **Zhu 2025 的数据仅两天**：用 RTL-SDR 硬件 + RTL1090 本地接收 ADS-B，第一天 1,082 架次、第二天 51 架次；人口数据为百度慧眼（1 km 网格，样例 Fig.3 为 2023-12-19 13:00）。已在 §2.1 补入"two-day campaign"。
- **Pretto 的"不可全年计算"**：这恰好支撑本文的研究缺口论证——即便是当前最先进方法也只能靠代表日抽样。

### 5.3 未匹配文件已解决

`output_txt/1-s2.0-S136192091530256X-main.txt` = **Fujiwara et al. (2017)**（ScienceDirect PII `S136192091530256X` 与 DOI `10.1016/j.trd.2017.07.015` 对应）。**本地有全文，可核验**：

| 断言 | 原文 | 判定 |
|---|---|---|
| 66 dB 与 happiness / relaxation 显著负相关 | "significant negative association between aircraft noise at the 66 dB level and both happiness and relaxation" | ✅ 成立 |
| 效应大于通勤 | "negative association ... and relaxation was greater than the negative eﬀects of travelling or commuting (β = −4.087)" | ✅ 成立，但**仅指 relaxation**；happiness 的比较对象是排队（β=−3.802）与照护成人（β=−4.527） |
| 终端内非工作情形：幸福感升、放松感降 | 非工作目的在机场：relaxation 效应 "more than twice the negative coeﬃcient for doing housework and chores"；happiness 相当于喝茶/咖啡（β=1.353）与看电视 | ✅ 成立，已按原文重写 |
| 百万级 ESM 样本 | "more than one million observations from tens of thousands of individuals in the UK"（Mappiness，2010-08 起） | ✅ 成立；人群为**全英**，研究范围为英格兰机场（已按原文措辞） |

### 5.4 去除的无出处推断（P1）

以下推断在初稿中以陈述句出现，但**仅有题录信息（[T]），本地无全文**，无法支撑如此具体的因果表述，已改为可支撑的写法：

| 条目 | 原表述（无支撑） | 改后 |
|---|---|---|
| `giladi2024validating` + `simons2022comparative` | "model–measurement agreement **depends strongly on how completely the operational population is represented, not merely on the acoustic formulation**" | "dedicated validation exercises have compared modelled against measured noise for the traffic of major European hubs" |
| `wunderli2022aircraft` | "sound incidence at shallow angles, **which dominates the near-field**" | "sound incidence at grazing angles" |
| `simons2026contribution` | "individual types contribute unequally to the measured noise field **even at identical flight paths**" | "measured noise levels across a monitoring network are attributable to aircraft types in unequal measure" |
| `hu2020spatial` | "airport environmental performance varies systematically **in ways that traffic volume alone does not explain**" | "aircraft CO₂ emissions have been shown to differ spatially across individual Chinese airports" |
| `wu2021evaluating` | "economic and ecological effects of airport policy **do not align automatically**" | "economic and ecological outcomes need not move together" |
| `heyes2021case` | "prevailing management processes **are organised around the operator's internal logic**" | 按原文改为：主张将噪声管理重构为 design-led、human-centric 流程。原文依据："Human-Centric and End-user focused, to produce outcomes that seek to benefit residents in an empathetic way"（ANIMA 项目） |
| `yang2023status` + `wang2024noise` | "indicating that **the methodological gap between international and Chinese practice has been closing**" | "complement this technical work by documenting the institutional context in which it is applied" |

### 5.5 两处引用错配（P0）

1. **`yang2023status` 被错用**：该文是《机场噪声控制的现状与挑战》，与"中国已有多个动态人口数据平台"无关。原 §2.3 用它支撑该论断属**错误引用**，已删除该引用并改写句子。
2. **`einicke2025predicting` 被错归为中国研究**：作者 Einicke, Karina & Kennedy, John（*Applied Acoustics*），**非中国团队**。原列于"中国贡献"段落，已移出并并入国际前沿趋势句。

### 5.6 复核结果

- 引用完整性：bib 30 条 vs 正文 30 条，**无缺失、无闲置**
- 花括号平衡：通过
- §2 正文 21 段，**无一段以 `\cite` 开头**
- §2 篇幅：1,987 → 2,012 词

### 5.7 仍未解决的问题（不在本轮范围）

- **论文摘要与方法的数据声称与实际采集不符**：摘要与 §3.2 称 "1-second resolution"、"one-year period (Jan–Dec 2025)"、"OpenSky Network"；而实际采集为**双源（adsb.lol + OpenSky）、30 秒间隔、14 天**（2026-10-11 起）。用户指示本轮只改综述，此项**未改**，但属 P0，投稿前必须处理。
- 6 条中文期刊文献仍为 `[VERIFY]`（CNKI 未注册 Crossref），需人工核对卷期页。
