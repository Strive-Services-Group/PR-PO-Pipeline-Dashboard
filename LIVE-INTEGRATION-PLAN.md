# PR / PO Pipeline Dashboard — Live Integration Plan

Goal: make the dashboard auto-refresh from D365 instead of uploading `pr.xlsx` / `po.xlsx` to the repo, reproducing the 16 columns from your D365 export — including **Step name** and **Step date and time**.

This document records what was verified against the live system on 2026-06-25 and the concrete build path.

---

## 1. Key finding — why the Resident 360 approach can't be copied as-is

| | Resident 360 dashboard | PR / PO dashboard |
|---|---|---|
| Data system | **Dataverse** (`<org>.crm.dynamics.com`) | **D365 Finance & Operations** (`ifahr-live.operations.uae.dynamics.com`) |
| Browser → API directly? | Yes — Dataverse serves CORS for token calls | **No — blocked by CORS (verified)** |

Test performed: from `https://chandansah605.github.io`, a direct `fetch()` to the F&O OData endpoint throws `TypeError: Failed to fetch`. F&O does not return `Access-Control-Allow-Origin` for the GitHub Pages origin and gives no admin setting to whitelist it.

**Conclusion:** F&O data must be reached through a small server-side component. The data itself is fully reachable (all 16 columns were located this session) — just not directly from the browser.

---

## 2. Column → live-source mapping (verified on PR-001539)

| # | Export column | Live source | Notes |
|---|---|---|---|
| 1 | Purchase requisition | `PurchaseRequisitionHeaders.RequisitionNumber` | direct |
| 2 | Quotation reference | `PurchaseRequisitionHeaders.IFAHRQuotationReference` | custom field |
| 3 | Name | `PurchaseRequisitionHeaders.RequisitionName` | direct |
| 4 | Preparer | `PurchaseRequisitionHeaders.PreparerPersonnelNumber` | number → resolve to name via a worker entity |
| 5 | Project ID | `PurchaseRequisitionHeaders.DefaultProjectId` | direct |
| 6 | Status | `PurchaseRequisitionHeaders.RequisitionStatus` | enum (e.g. `InReview`) |
| 7 | Created date | not on header OData | finalize during build (RecId metadata / workflow) |
| 8 | Submitted date | workflow first-submission event | finalize during build |
| 9 | Accepted By/Assign To | `WorkflowWorkItems` (claimed/assigned user) | |
| 10 | Department | `PurchaseRequisitionLines` financial dimension | parse `DefaultLedgerDimensionDisplayValue` |
| 11 | Location | `PurchaseRequisitionLines` delivery address / project | |
| 12 | Contract | `PurchaseRequisitionLines` dimension | values: `Contracted` / `Variation` |
| 13 | Total amount | `SUM(PurchaseRequisitionLines.LineAmount)` per PR | aggregate |
| 14 | Pending Approver/User | `WorkflowWorkItems.UserId` where `Status = Pending` | |
| 15 | Step name | `WorkflowWorkItems.ElementId` → name map | see §3 |
| 16 | Step date and time | `WorkflowWorkItems` datetime | `DueDateTime` / created |

Entity facts:
- Filter PR work items with `MenuItemName eq 'PurchReqTable'` (Document Class Id `29903`).
- `WorkflowWorkItems` keeps **both** `Pending` and `Completed` rows, so current step and history are available.
- `PurchaseRequisitionHeaderV2` returns 404 in this environment — only the basic `PurchaseRequisitionHeaders` (15 fields) is published.

---

## 3. Step name resolution (the one non-trivial column)

The list-page "Step name" you see (e.g. `PurchReqReviewTask`, `Unit prices updated in PR lines`, `Finance & Accounts_Accounting Manager`, `Procurement sends inquiry/RFQ to suppliers`) is **not** stored as text in OData — only as `ElementId` / `StepId` GUIDs.

Important: a single step name maps to **multiple GUIDs** because each published workflow version has its own element GUIDs. So a hand-built static map is brittle.

Recommended resolution (done in the proxy, where there's no browser redaction and full paging):
1. Pull distinct `(ElementId, Subject)` for `MenuItemName eq 'PurchReqTable'`.
2. Resolve the readable element name from the workflow element/version metadata, **or** maintain an auto-rebuilt `ElementId → StepName` lookup keyed off recent items.
3. Cache the map; refresh it on a schedule.

There are ~17 distinct readable step names in your current data.

---

## 4. Architecture — Proxy API (chosen approach)

```
GitHub Pages dashboard ──fetch──▶  Azure Function (HTTPS, CORS allowed)
                                        │  client-credentials token
                                        ▼
                              D365 F&O OData (/data/...)
```

- The Function holds an Entra app registration (client id + secret) and gets a token for the F&O resource.
- It queries the three entities, assembles the 16 columns (incl. step-name map), and returns JSON.
- It sets `Access-Control-Allow-Origin: https://chandansah605.github.io`.
- It caches the result ~3 min; the dashboard polls every 3 min.
- No secrets ever reach the browser.

---

## 5. Azure runbook — your steps (I cannot do these for you)

1. **Entra app registration** (portal → Microsoft Entra ID → App registrations → New):
   - Name e.g. `pr-po-dashboard-proxy`. Record **Application (client) ID**.
   - Certificates & secrets → New client secret → record the **value**.
   - Tenant ID: `f9454680-0e71-4aca-8616-3a77bda74f7e`.
2. **Grant the app access in F&O**: System administration → Setup → **Azure Active Directory applications** → New → enter the Client ID, a name, and a user account with read access to purchase requisitions.
3. **Deploy the Function** (code in §6): create a Function App (Node 20), deploy, set application settings:
   - `TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET`, `FO_RESOURCE = https://ifahr-live.operations.uae.dynamics.com`, `ALLOWED_ORIGIN = https://chandansah605.github.io`.
4. **CORS**: either rely on the headers the Function sets, or add the origin under the Function App CORS blade.
5. **Dashboard**: I update it to call the Function URL and auto-refresh; you commit + push in GitHub Desktop.

---

## 6. What I will build next (no Azure access needed)

- The Azure Function (Node) — token acquisition, the three OData queries, column assembly, step-name map, CORS, caching. Delivered as repo files.
- The dashboard data-layer change — replace the Excel load with a proxy fetch + 3-minute refresh, keeping current filters and auth.
- A small finalisation pass on Created/Submitted dates and the Department/Location/Contract dimension parsing, against live data.

---

## 7. Option 3 — Dataverse virtual entities (assessed)

You can expose F&O tables in Dataverse via **Finance and Operations virtual entities**, then reuse the Resident 360 browser pattern (Dataverse serves CORS). Caveat: virtual entities are read-only and the **workflow work items / Step name are unlikely to be available** as virtual entities — so you'd still need the proxy for the step columns. Net: virtual entities can help for header/line data but don't remove the proxy for the workflow step. Proxy remains the primary path.
