# DX Freight Routing System - Issues & Enhancements

## Bugs

### BUG-001: Depots with zero capacity receiving allocations
- **Status:** Open
- **Priority:** High
- **Description:** Depots with a capacity of 0 are being allocated parcels. A depot with zero capacity should never receive any freight - they should be skipped entirely in the routing logic.
- **Found:** 2026-01-06
- **Example:** BASILDON, CARLISLE, LDE 89 CHANCERY LANE all showing 0 capacity but receiving parcels


## Enhancements (Stage 2)

- Historical reporting (compare today vs last week/month)
- Alerts when depots hit capacity thresholds
- What-if scenario planning
- Bulk override imports
- Saved views/favourites
- Export functionality (PDF/Excel)
- Email notifications


## Enhancements (Stage 3)

- Time-based capacity (capacity decreases through the day)
- Collection time windows
- Transit time estimates
- Real-time updates

### BUG-002: Override page shows all collection points
- **Status:** Open
- **Priority:** Medium
- **Description:** The Collection Point dropdown on the Overrides page shows all 406 CPs. It should only show CPs that have volumes for the selected date.
- **Found:** 2026-01-06

## Minor Enhancements

### ENH-001: Add cost per parcel to Expected Costs page
- **Status:** Open
- **Priority:** Low
- **Description:** Add a "Cost per Parcel" column to both the Collection Point and Depot tables on Expected Costs page. Calculation: Total Cost / Parcels
- **Found:** 2026-01-06

### BUG-003: Dashboard map shows all CPs and depots
- **Status:** Open
- **Priority:** Medium
- **Description:** Map should only show CPs with collections for the selected date and depots that are receiving freight that day, not all 407 CPs and 52 depots.

### BUG-004: View Details button on Depot Allocations not working
- **Status:** Open
- **Priority:** High
- **Description:** Clicking View Details does nothing

### ENH-002: Clickable records for filtering
- **Status:** Open
- **Priority:** Medium
- **Description:** 
  - Collections: Click CPID to filter all trailers for that CP, click depot to show all CPs routing there
  - Depot Allocations: Click depot to show all incoming CPs
  - Expected Costs: Add filter dropdowns for CPID, depot, collection point
  