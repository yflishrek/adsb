# OpenSky Trino Database Access Application

## Research Data Access Request — Academic/Non-Commercial Use

---

### 1. Applicant Information

| Field | Details |
|-------|---------|
| **Full Name** | [请填写您的姓名] |
| **Institution** | Civil Aviation University of China (中国民航大学) |
| **Faculty/Department** | College of Air Traffic Management (空中交通管理学院) |
| **Position** | [请填写：博士生/硕士生/研究人员] |
| **Supervisor** | Prof. Zhao Yifei (赵嶷飞教授) |
| **Institution Email** | [您的 edu 邮箱] @[学校域名].edu.cn |
| **Application Date** | 2026-10-09 |

---

### 2. Research Project Overview

**Project Title:**
Operational Noise Exposure Assessment in the Beijing-Tianjin-Hebei (BTH) Multi-Airport Region Based on INM/AEDT Modeling

**Project Summary:**
This research aims to assess the aviation noise exposure patterns in the Beijing-Tianjin-Hebei (BTH) region, which is one of the world's most complex multi-airport systems. The study area encompasses three major international airports operating in close proximity:

| ICAO | Airport | IATA | Annual Traffic |
|------|---------|------|----------------|
| ZBAA | Beijing Capital International Airport | PEK | ~100M passengers |
| ZBAD | Beijing Daxing International Airport | PKX | ~45M passengers |
| ZBTJ | Tianjin Binhai International Airport | TSN | ~20M passengers |

The research uses the FAA's Integrated Noise Model (INM) and Aviation Environmental Design Tool (AEDT) to simulate noise exposure contours, requiring accurate flight operations data as primary input.

**Research Supervisor:**
Prof. Zhao Yifei is a full professor at the College of Air Traffic Management, Civil Aviation University of China. His research focuses on air traffic management, aviation environmental impact assessment, and multi-airport system optimization. He has published extensively in leading aviation journals and has supervised multiple research projects on aviation noise.

---

### 3. Research Objectives

1. **Quantity noise exposure levels** around each of the three BTH airports based on actual flight operations
2. **Compare noise contour patterns** between the three airports and analyze cumulative exposure in overlapping areas
3. **Evaluate the impact of fleet mix and operational procedures** on the community noise footprint
4. **Validate noise simulation results** against reported noise complaints and monitoring data

---

### 4. Data Requirements

#### 4.1 Target Period
**January 6, 2025 — January 19, 2025 (14 days)**

This period was selected to capture:
- Normal winter operational patterns
- A full two-week cycle including both weekdays and weekends
- Representative traffic conditions without major holidays or weather disruptions

#### 4.2 Airports of Interest

| ICAO Code | Airport Name | Type |
|-----------|-------------|------|
| ZBAA | Beijing Capital International | Large hub |
| ZBAD | Beijing Daxing International | Large hub |
| ZBTJ | Tianjin Binhai International | Medium hub |

#### 4.3 Required Data Fields

From the `flights_data4` and `state_vectors_data4` tables, the following fields are needed:

| Data Field | Purpose in Noise Modeling |
|------------|--------------------------|
| **callsign** | Flight identification, cross-reference with airline schedules |
| **icao24** | Aircraft identification for type lookup |
| **departure airport** | Origin identification |
| **destination airport** | Arrival identification |
| **first seen / last seen** | Actual operation timestamps |
| **time at position** | Noise event timing |
| **latitude / longitude** | Aircraft position relative to noise monitors |
| **barometric altitude** | Altitude input for noise model (distance attenuation) |
| **velocity** | Thrust/power setting estimation for noise calculations |
| **vertical rate** | Climb/descent profile for noise segment modeling |
| **on ground flag** | Distinguish airborne from ground operations |

#### 4.4 Query Scope

The data will be queried within a bounding box covering the BTH region:
- **Latitude**: 38.5°N — 41.0°N
- **Longitude**: 116.0°E — 118.5°E

---

### 5. Methodology

The workflow for this research is as follows:

```
┌─────────────────────────────────────────────────┐
│  Step 1: Data Collection (via OpenSky Trino)     │
│  └─ Query state_vectors_data4 for BTH region     │
│     within Jan 6–19, 2025                         │
├─────────────────────────────────────────────────┤
│  Step 2: Data Processing & Filtering              │
│  └─ Identify individual flights                   │
│  └─ Filter by airport (ZBAA/ZBAD/ZBTJ)           │
│  └─ Extract aircraft type from ICAO24             │
├─────────────────────────────────────────────────┤
│  Step 3: Noise Modeling                           │
│  └─ Prepare flight operations input file          │
│  └─ Configure INM/AEDT with BTH airport layouts   │
│  └─ Run noise simulations                         │
├─────────────────────────────────────────────────┤
│  Step 4: Analysis & Validation                    │
│  └─ Generate noise contour maps                   │
│  └─ Analyze population exposure                   │
│  └─ Validate against monitoring data              │
└─────────────────────────────────────────────────┘
```

---

### 6. Data Usage Commitment

- ✅ **Non-commercial purpose**: This data will be used exclusively for academic research and education at the Civil Aviation University of China.
- ✅ **No redistribution**: The data will not be shared, sold, or redistributed to third parties. Only aggregated results (noise contours, statistical summaries) will be published.
- ✅ **Attribution**: Any publications resulting from this research will acknowledge the OpenSky Network as the data source.
- ✅ **Data retention**: Raw data will be stored securely and retained only for the duration of the research project.
- ✅ **Compliance**: The research team will comply with all OpenSky data usage policies and terms of service.

---

### 7. Expected Outputs

| Deliverable | Description |
|-------------|-------------|
| Peer-reviewed journal article | At least one paper submitted to a leading aviation/acoustics journal |
| Noise exposure contour maps | Visual representation of noise levels around the three BTH airports |
| Comparative analysis report | Comparison of noise impacts across the multi-airport system |
| Open data (aggregated results) | Anonymized noise exposure data for community reference |

---

### 8. Contact Information

**Primary Applicant:**
[您的姓名]
[您的英文名，如可用]
PhD Candidate / Master Student
College of Air Traffic Management
Civil Aviation University of China
Email: [您的 edu 邮箱]

**Research Supervisor:**
Prof. Zhao Yifei (赵嶷飞)
College of Air Traffic Management
Civil Aviation University of China
Email: [赵教授的邮箱，如有]

---

### Declaration

*I hereby declare that the information provided in this application is accurate and complete. I commit to using the requested data solely for the non-commercial academic research purposes described above, in compliance with the OpenSky Network's data usage policies.*

**Signature:** ________________________ **Date:** ________________________

---

## 中文参考 (Chinese Reference)

### 申请要点中文说明
（以上内容为正式的英文申请，以下为中文要点帮助您填写在线表单）

**项目名称：** 基于INM/AEDT的京津冀多机场区域运行噪声暴露评估研究

**研究机构：** 中国民航大学 空中交通管理学院

**导师：** 赵嶷飞教授

**研究摘要：**
本研究旨在评估京津冀（BTH）多机场区域的航空噪声暴露模式。BTH地区是全球最复杂的多机场系统之一，涵盖北京首都（ZBAA）、北京大兴（ZBAD）和天津滨海（ZBTJ）三大国际机场。研究使用 FAA 综合噪声模型（INM）和航空环境设计工具（AEDT）进行噪声暴露模拟，需要准确的航班运行数据作为模型输入。

**所需数据：** 2025年1月6日至19日，BTH区域内（38.5°N–41.0°N, 116.0°E–118.5°E）的 state_vectors_data4 表数据。

**数据用途承诺：** 仅用于中国民航大学的非商业学术研究，不向第三方分发，发表时将注明数据来源为 OpenSky Network。

---

*Document prepared by research team at Civil Aviation University of China, College of Air Traffic Management*