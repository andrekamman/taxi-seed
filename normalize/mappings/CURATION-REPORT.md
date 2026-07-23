# Mapping curation report (2026-07-23)

Machine-accepted by `taxi-curate-mappings`. **Verify the acknowledgment-required
decisions** below (lossy casts and data-loss drops); renames are heuristic.

## yellow

### Acknowledgments required (verify these)
- **lossy cast** `passenger_count`: DOUBLE -> BIGINT (55 file(s))
- **lossy cast** `RatecodeID`: DOUBLE -> BIGINT (55 file(s))
- **data loss** `vendor_name`: had data in 12 file(s)
- **data loss** `Trip_Dropoff_DateTime`: had data in 12 file(s)
- **data loss** `Start_Lon`: had data in 12 file(s)
- **data loss** `store_and_forward`: had data in 12 file(s)
- **data loss** `End_Lon`: had data in 12 file(s)
- **data loss** `Trip_Pickup_DateTime`: had data in 12 file(s)
- **data loss** `End_Lat`: had data in 12 file(s)
- **data loss** `Start_Lat`: had data in 12 file(s)
- **data loss** `rate_code`: had data in 12 file(s)
- **data loss** `pickup_latitude`: had data in 12 file(s)
- **data loss** `dropoff_datetime`: had data in 12 file(s)
- **data loss** `dropoff_latitude`: had data in 12 file(s)
- **data loss** `pickup_longitude`: had data in 12 file(s)
- **data loss** `dropoff_longitude`: had data in 12 file(s)
- **data loss** `pickup_datetime`: had data in 12 file(s)
- **data loss** `vendor_id`: had data in 12 file(s)
- **data loss** `__index_level_0__`: had data in 2 file(s)

### Auto-accepted renames (heuristic)
- `Total_Amt` -> `total_amount` (confidence 81%)
- `Payment_Type` -> `payment_type` (confidence 66%)
- `Passenger_Count` -> `passenger_count` (confidence 84%)
- `Tolls_Amt` -> `tolls_amount` (confidence 92%)
- `Tip_Amt` -> `tip_amount` (confidence 96%)
- `surcharge` -> `extra` (confidence 92%)
- `Fare_Amt` -> `fare_amount` (confidence 99%)
- `Trip_Distance` -> `trip_distance` (confidence 95%)

## green

### Acknowledgments required (verify these)
- **lossy cast** `trip_type`: DOUBLE -> BIGINT (97 file(s))
- **lossy cast** `RatecodeID`: DOUBLE -> BIGINT (55 file(s))
- **lossy cast** `passenger_count`: DOUBLE -> BIGINT (55 file(s))
- **lossy cast** `payment_type`: DOUBLE -> BIGINT (55 file(s))

### Auto-accepted renames (heuristic)
- none

## fhv

### Acknowledgments required (verify these)
- **lossy cast** `PUlocationID`: DOUBLE -> BIGINT (95 file(s))
- **lossy cast** `DOlocationID`: DOUBLE -> BIGINT (94 file(s))
- **lossy cast** `SR_Flag`: DOUBLE -> BIGINT (22 file(s))

### Auto-accepted renames (heuristic)
- none

## fhvhv

### Acknowledgments required (verify these)
- none

### Auto-accepted renames (heuristic)
- none

