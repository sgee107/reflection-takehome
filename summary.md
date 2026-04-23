# Data Summary — ProcureAI Scenario Databases

## Dataset Overview

6 SQLite scenario databases sharing the same product portfolio (4 products), component catalog (19 components), and supplier network (13 suppliers, 12 approved). Each scenario varies production schedule, inventory levels, or existing procurement to test different planning challenges.

All scenarios dated **2025-09-01** except Scenario 05 (**2025-10-05**).

### At a Glance

| Scenario | Units | Orders | Days Avail | Existing POs | Components Short | Primary Challenge |
|----------|-------|--------|------------|--------------|------------------|-------------------|
| 01 Baseline | 63 | 4 | 11–39 | 0 | 13/19 | Standard capacity planning |
| 02 Partial | 63 | 4 | 11–39 | 4 | 13/19 | Residual gap identification |
| 03 Tight | 113 | 5 | 9–39 | 0 | 17/19 | Timeline achievability |
| 04 Low Inv | 63 | 4 | 11–39 | 0 | 19/19 | Full depletion recovery |
| 05 Competing | 90 | 4 | 27–36 | 0 | 17/19 | Multi-product demand conflict |
| 06 Simple | 10 | 1 | 44 | 0 | 2/19 | Happy-path baseline |

---

## Products (Identical Across All Scenarios)

| ID | Name | Category | Price | BOM Components |
|----|------|----------|-------|----------------|
| FG-1001 | PowerDrive 3000 | Motors | $2,450 | 12 components |
| FG-1002 | PowerDrive 5000 | Motors | $4,100 | 14 components (most complex) |
| FG-1003 | ControlHub X1 | Controllers | $1,850 | 8 components |
| FG-1004 | SensorArray Pro | Sensors | $980 | 7 components (simplest) |

### Key BOM Relationships
- **FG-1001 & FG-1002** share 12 components (motors compete heavily for magnets, MOSFETs, steel)
- **FG-1002** is the only product using transformer cores (CMP-018) and capacitor banks (CMP-017)
- **FG-1004** has unique demand for temperature/pressure/humidity sensors and IP67 housings
- **FG-1003 & FG-1004** are lighter on raw materials, heavier on electronic components

---

## Components — Special Handling Requirements

**Requires ISO-9001 certification from supplier:**
- CMP-005: PCB Assembly (6-layer)

**Hazardous materials (special receiving/handling, FIFO rotation):**
- CMP-010: Thermal Compound (stored in Hazmat-Storage)
- CMP-011: Conformal Coating (stored in Hazmat-Storage)

**Chronically low inventory across scenarios:**
- CMP-011 Conformal Coating: 5 units (baseline)
- CMP-010 Thermal Compound: 8 units (baseline)
- CMP-008 Aluminum Housing: 15 units (baseline)
- CMP-014 Pressure Transducer: 20 units (baseline)

---

## Supplier Network

### By Tier
| Tier | Suppliers | Characteristics |
|------|-----------|-----------------|
| Strategic | SUP-101 (Sterling), SUP-104 (Bayern), SUP-112 (Pinnacle) | Long-term, premium pricing, high reliability |
| Preferred | SUP-102 (Pacific), SUP-105 (Great Lakes), SUP-108 (MagnetPro) | Good balance of cost/reliability |
| Standard | SUP-103 (Shenzhen), SUP-106 (TechSource), SUP-107 (Nanjing), SUP-109 (QuickParts), SUP-110 (EcoBoard), SUP-111 (Delta) | Cost-competitive or niche |
| **Removed** | **SUP-113 (Jiangsu Electronics)** | **Removed 2025-01-15, quality issues — must not be used** |

### Domestic vs International
- **9 domestic** (USA): SUP-101, 102, 105, 106, 108, 109, 111, 112 + Canada: SUP-110
- **3 international**: SUP-103 (China), SUP-104 (Germany), SUP-107 (China)

### Lead Time Extremes
- **Fastest**: SUP-106 TechSource Direct — 3 days (electronics distributor)
- **Slowest**: SUP-107 Nanjing Rare Earth — 35 days (ocean freight for magnets)
- **Fastest magnets**: SUP-108 MagnetPro — 14 days domestic (but limited N52 capacity, higher price $5.80 vs $3.25)

---

## Scenario Details

### Scenario 01 — Baseline
Standard case: 4 production orders, no existing POs, normal inventory. Tests core procurement planning.

**Production Schedule:**
| Order | Product | Qty | Customer | Deadline | Days |
|-------|---------|-----|----------|----------|------|
| PO-5001 | PowerDrive 3000 | 25 | Hartfield Industries | 2025-09-12 | 11 |
| PO-5003 | SensorArray Pro | 20 | EnviroTech Solutions | 2025-09-15 | 14 |
| PO-5002 | ControlHub X1 | 10 | Meridian Controls | 2025-09-22 | 21 |
| PO-5004 | PowerDrive 5000 | 8 | Atlas Robotics | 2025-10-10 | 39 |

**Critical shortfalls:** Magnets (need 328, have 120), MOSFETs (need 270, have 200), sensor components (transducers, housings, humidity sensors all short).

**Timeline risk:** PO-5001 due in 11 days but magnet lead times are 14–35 days.

### Scenario 02 — Partial Procurement
Same orders as Scenario 01, but 4 POs already placed:
- 150 magnets incoming (100 from Nanjing arriving 09-08, 50 from MagnetPro arriving 09-02)
- 50 PCBs incoming from Sterling (arriving 09-01)
- 200 steel laminations from Great Lakes (arriving 09-02)

**Tests:** Identifying residual gaps after partial fulfillment. Magnet dual-sourcing strategy already visible.

### Scenario 03 — Tight Timeline
Adds urgent order **PO-5005: 50 × ControlHub X1 due 2025-09-10 (9 days)**. Total demand jumps to 113 units.

**Impact:** PCB demand +123%, microcontroller +189%, MOSFET +74%, connectors +149%. The 9-day deadline for PO-5005 is likely unachievable for PCBs (fastest lead is 10 days). Tests escalation logic and partial-fulfillment decisions.

### Scenario 04 — Low Inventory
Same orders as Scenario 01, but inventory depleted to near-zero across all components (e.g., magnets: 120 → 15, MOSFETs: 200 → 25, steel: 200 → 30). **All 19 components** are short. Tests bulk procurement and safety-stock rebuilding.

### Scenario 05 — Competing Demand
Larger quantities, later dates (27–36 days), different current date (2025-10-05).

| Order | Product | Qty | Deadline |
|-------|---------|-----|----------|
| PO-5001 | PowerDrive 3000 | 30 | 2025-11-01 |
| PO-5002 | PowerDrive 5000 | 20 | 2025-11-01 |
| PO-5003 | ControlHub X1 | 15 | 2025-11-10 |
| PO-5004 | SensorArray Pro | 25 | 2025-11-10 |

**Magnet crisis:** 560 needed (highest across all scenarios). Both motor products competing for the same constrained supply. Tests multi-supplier orchestration and demand aggregation.

### Scenario 06 — Simple
Single order: 10 × SensorArray Pro, due 2025-10-15 (44 days). Only 2 components short (pressure transducers and sensor housings), both easily sourced domestically within timeline. **Use this for initial development and testing.**

---

## Key Observations for Agent Design

1. **BOM explosion is the foundation** — every decision starts with `production_schedule × bom` to compute component demand, then subtracting `inventory` and incoming `purchase_orders`.

2. **Lead time feasibility is the hardest constraint** — several scenarios have deadlines shorter than supplier lead times. The agent must flag these as alerts rather than placing impossible orders.

3. **Magnet sourcing is the recurring bottleneck** — appears in every multi-product scenario. Dual-sourcing (Nanjing + MagnetPro) is the pattern, with tradeoffs between cost ($3.25 vs $5.80) and lead time (35d vs 14d).

4. **SUP-113 is a trap** — cheapest PCB supplier ($6.00) but removed from approved list. Agent must check `on_approved_list` before selecting.

5. **Scenario 02's existing POs demonstrate the gap-analysis pattern** — the agent must account for in-flight procurement, not just inventory.

6. **Hazmat components have low stock everywhere** — thermal compound and conformal coating consistently short, requiring special handling flags.

7. **Recommended testing order:** Scenario 06 (validate basics) → 01 (standard complexity) → 02 (PO reconciliation) → 04 (stress inventory) → 05 (competing demand) → 03 (timeline pressure + alerts).
