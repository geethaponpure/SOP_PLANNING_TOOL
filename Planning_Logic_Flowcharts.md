# Supply Planning — Flowcharts (Mermaid)

Companion to **Planning_Logic_and_Assumptions.docx**. These render graphically in VS Code
(Markdown preview), GitHub, or any Mermaid viewer, and can be exported to PNG/SVG.

---

## 1. End-to-end planning pipeline

```mermaid
flowchart TD
    A["1. TIME — pick planning JC + acc year<br/>(3rd-week-Monday advance rule)"] --> B
    B["2. DATA — CRM read-only SELECTs<br/>projection · pending SOC · dispatch · stock · segments · PTS/PTO · PO"] --> C
    C["3. MSL — 13-JC dispatch → per-name MSL<br/>freq &gt; 10 &amp; customers &gt; 5 · MSL = 50% of avg one-JC"] --> D
    D["4. DEMAND — current = Projection(WK1+WK2) + MFG SOC"] --> E
    E["5. FILTER — division scope + qualify(&gt;25KG) + MSL top-ups"] --> F
    F["6. BOM — select preferred variant + classify activity"] --> G
    G["7. REQUIRE — Mfg Req = (demand + MSL) − On-hand(WH+Branch)"] --> H
    H["8. RM — gross → net-to-buy (stock+subs+in-transit), timed by lead time"] --> I
    I["9. MAKE? — Producible (PTS-first) allocation of shared RM"] --> J
    J["10. EXPLODE — intermediates → leaf RMs · consolidate · EXPORT"]
```

---

## 2. JC time model — choosing the JC and the SOC window

```mermaid
flowchart TD
    T["today's date"] --> C{"Which JC<br/>contains today?"}
    C --> CUR["current JC (e.g. JC4)"]
    CUR --> Q{"today ≥ 3rd-week<br/>Monday of current JC?"}
    Q -- No --> P1["Plan the CURRENT JC"]
    Q -- Yes --> P2["Plan the NEXT JC (JC5)<br/>(projection just approved)"]
    P1 --> H["Horizon = planning JC, +1, +2"]
    P2 --> H
    H --> W["SOC window:<br/>end = close of JC before planning JC<br/>start = 1900-01-01 (all open pending)"]
```

---

## 3. Demand build — manufacturing requirement

```mermaid
flowchart TD
    PR["Projection (JC WK1+WK2)<br/>approved, status 5"] --> DEM
    SOC["MFG SOC Pending<br/>open sale orders, planning orgs"] --> DEM
    DEM["Current-JC demand<br/>= Projection + MFG SOC"] --> REQ
    MSL["MSL buffer<br/>(valid items only)"] --> REQ
    OH["On-hand FG<br/>Warehouse + Branch"] --> REQ
    REQ["Mfg Required (Current)<br/>= max(0, demand + MSL − On-hand)"]
```

---

## 4. Item selection & MSL top-up decision

```mermaid
flowchart TD
    S["candidate product"] --> X{"Excel upload?"}
    X -- Yes --> PLAN["ALWAYS planned<br/>(Excel qty; SOC not re-added)"]
    X -- No --> SC{"In division scope<br/>(Segment1)?"}
    SC -- No --> DROP1["DROP (out of scope)"]
    SC -- Yes --> QL{"Qualifies?<br/>proj &gt; 25KG or SOC &gt; 25KG"}
    QL -- Yes --> PLAN2["PLAN it"]
    QL -- No --> MC{"MSL top-up?<br/>valid MSL · has BOM ·<br/>on-hand &lt; MSL · in scope"}
    MC -- "all yes" --> INJ["INJECT as MSL top-up<br/>demand ≈ MSL − on-hand"]
    MC -- "any no" --> DROP2["DROP"]
```

---

## 5. Activity classification

```mermaid
flowchart TD
    B["BOM_TYPE"] --> I{CONVERSION / DECODE?}
    I -- Yes --> INT["internal (alias, not planned)"]
    I -- No --> M{MFG?}
    M -- Yes --> MAN["manufacturing"]
    M -- No --> R{REPACK / RELABEL?}
    R -- Yes --> REP["repack_relabel"]
    R -- No --> U{"untagged — recipe shape?"}
    U -- "bulk, no packing comp" --> MAN
    U -- "≥2 chem comps, one fractional" --> MAN
    U -- "any 1:1 component" --> REP
    U -- "else" --> UNC["unclassified"]
    NB["no usable BOM"] --> TR["trading (product level)"]
```

---

## 6. Component requirement & net-to-buy

```mermaid
flowchart TD
    G["gross = mfg qty × qty-per-unit (per JC)"] --> AV
    AV["available = main stock + substitutes + PO in-transit<br/>(packing &amp; DM water dropped)"] --> NET
    NET["net-to-buy = waterfall(gross vs available)<br/>cover Current → Next1 → Next2"] --> LT
    LT["lead time = avg of latest 5 POs<br/>fallbacks: same-material code → decode encoded name"] --> BK
    BK["buy horizon: ≤30d Current | ≤60d +Next1 | &gt;60d/unknown all 3"] --> BUY
    BUY["buy the allowed JC buckets where net &gt; 0"]
```

---

## 7. Producible — PTS-first shared-RM allocation

```mermaid
flowchart TD
    POOL["Seed shared RM pool from availability<br/>(packing excluded)"] --> ORD
    ORD["Order products: PTS before PTO,<br/>then larger Current requirement first"] --> LOOP
    LOOP["For each product:"] --> CAP
    CAP["cap = min over RMs of pool[RM] / qty-per-unit"] --> MK
    MK["make = min(cap, Current Mfg-Required)"] --> DED
    DED["deduct make × qty from each shared RM<br/>leftover flows to next product"] --> LOOP
    CAP --> OUT["producible_qty = cap;<br/>coverage vs Mfg-Required"]
```

---

## 8. Real-RM explosion

```mermaid
flowchart TD
    C["component"] --> Q{"real recipe?<br/>(manufacturing/repack, not a rename)"}
    Q -- Yes --> REC["recurse into its BOM,<br/>multiply quantities down"]
    REC --> C
    Q -- No --> LEAF["leaf purchased RM → keep"]
    REC -.->|"depth &gt; 12 or cycle"| UNR["emit as leaf, flag UNRESOLVED"]
    LEAF --> DEC["decode encoded names for display/lead<br/>(LTLP005 → LUTENSOL TO5)"]
```

---

## 9. MSL computation

```mermaid
flowchart TD
    W["Window = latest 13 JCs (sliding)"] --> RU
    RU["Roll up all CODES of a product NAME:<br/>dispatch · per-JC movement · unique customers · on-hand"] --> V
    V{"VALID?<br/>freq &gt; 10 of 13  AND  customers &gt; 5"}
    V -- No --> SKIP["excluded from MSL"]
    V -- Yes --> M["MSL = 50% × average one-JC sales<br/>(total / 13 × 0.5)"]
    M --> ACT["Activity = class with most dispatch volume<br/>(ties → Manufacturing first)"]
```
