# Common abbreviation expansions for taxi data
ABBREVIATIONS = {
    "amt": "amount",
    "lat": "latitude",
    "lon": "longitude",
    "long": "longitude",
    "fwd": "forward",
    "id": "id",
    "num": "number",
    "cnt": "count",
    "dist": "distance",
    "dt": "datetime",
    "ts": "timestamp",
    "pu": "pickup",
    "do": "dropoff",
    "loc": "location",
    "src": "source",
    "dst": "destination",
    "pax": "passenger",
    "surchg": "surcharge",
    # Taxi-specific synonyms
    "start": "pickup",
    "end": "dropoff",
    "origin": "pickup",
    "destination": "dropoff",
    "trip": "",  # Often a prefix that can be ignored
    "tpep": "",  # Taxi prefix
    "lpep": "",  # Taxi prefix
}

# Semantic opposites that should NOT be matched together
SEMANTIC_OPPOSITES = [
    {"pickup", "dropoff"},
    {"start", "end"},
    {"origin", "destination"},
    {"pulocationid", "dolocationid"},
    {"pu", "do"},
]


def get_semantic_categories(name: str) -> set[str]:
    """Get the semantic categories of a column name."""
    lower = name.lower()
    categories = set()

    # Pickup vs dropoff
    pickup_indicators = ["pickup", "start", "origin", "pu_", "_pu", "pulocation", "tpep_pickup", "lpep_pickup"]
    dropoff_indicators = ["dropoff", "drop_off", "end", "destination", "do_", "_do", "dolocation", "tpep_dropoff", "lpep_dropoff"]

    for indicator in pickup_indicators:
        if indicator in lower:
            categories.add("pickup")
            break

    for indicator in dropoff_indicators:
        if indicator in lower:
            categories.add("dropoff")
            break

    # Coordinate columns (lat/lon)
    coord_indicators = ["lat", "lon", "latitude", "longitude", "_lat", "_lon"]
    for indicator in coord_indicators:
        if indicator in lower:
            categories.add("coordinate")
            break

    # Location ID columns
    if "locationid" in lower or "location_id" in lower:
        categories.add("location_id")

    # Amount/money columns
    amount_indicators = ["amount", "amt", "fare", "fee", "surcharge", "tax", "tip", "toll"]
    for indicator in amount_indicators:
        if indicator in lower:
            categories.add("amount")
            break

    # Datetime columns
    datetime_indicators = ["datetime", "date", "time", "timestamp"]
    for indicator in datetime_indicators:
        if indicator in lower:
            categories.add("datetime")
            break

    return categories


def normalize_column_name(name: str) -> str:
    """Normalize a column name for comparison."""
    # Lowercase
    normalized = name.lower()

    # Replace underscores with spaces for token splitting
    tokens = normalized.replace("_", " ").split()

    # Expand abbreviations
    expanded_tokens = []
    for token in tokens:
        expanded_tokens.append(ABBREVIATIONS.get(token, token))

    # Join back and remove all separators for final comparison
    return "".join(expanded_tokens)


def column_name_similarity(name1: str, name2: str) -> float:
    """Calculate similarity between two column names (0.0 to 1.0)."""
    cats1 = get_semantic_categories(name1)
    cats2 = get_semantic_categories(name2)

    # Check for semantic category conflicts
    # Pickup vs dropoff should never match
    if "pickup" in cats1 and "dropoff" in cats2:
        return 0.0
    if "dropoff" in cats1 and "pickup" in cats2:
        return 0.0

    # Coordinates should only match coordinates, location IDs should match location IDs
    if "coordinate" in cats1 and "coordinate" not in cats2 and cats2:
        return 0.0
    if "coordinate" in cats2 and "coordinate" not in cats1 and cats1:
        return 0.0
    if "location_id" in cats1 and "location_id" not in cats2 and cats2:
        return 0.0
    if "location_id" in cats2 and "location_id" not in cats1 and cats1:
        return 0.0

    # Amount columns should match amount columns
    if "amount" in cats1 and "amount" not in cats2 and cats2:
        return 0.0
    if "amount" in cats2 and "amount" not in cats1 and cats1:
        return 0.0

    # Datetime columns should match datetime columns
    if "datetime" in cats1 and "datetime" not in cats2 and cats2:
        return 0.0
    if "datetime" in cats2 and "datetime" not in cats1 and cats1:
        return 0.0

    norm1 = normalize_column_name(name1)
    norm2 = normalize_column_name(name2)

    # Exact match after normalization
    if norm1 == norm2:
        return 1.0

    # Check if one contains the other
    if norm1 in norm2 or norm2 in norm1:
        shorter = min(len(norm1), len(norm2))
        longer = max(len(norm1), len(norm2))
        return shorter / longer

    # Token-based similarity with expanded abbreviations
    tokens1 = set(ABBREVIATIONS.get(t, t) for t in name1.lower().replace("_", " ").split())
    tokens2 = set(ABBREVIATIONS.get(t, t) for t in name2.lower().replace("_", " ").split())

    # Remove empty tokens (from prefix expansions)
    tokens1 = {t for t in tokens1 if t}
    tokens2 = {t for t in tokens2 if t}

    if not tokens1 or not tokens2:
        return 0.0

    intersection = tokens1 & tokens2
    union = tokens1 | tokens2

    if not union:
        return 0.0

    jaccard = len(intersection) / len(union)

    # Calculate string similarity using longest common subsequence ratio
    lcs_len = longest_common_subsequence_length(norm1, norm2)
    lcs_similarity = (2 * lcs_len) / (len(norm1) + len(norm2)) if (norm1 and norm2) else 0

    return max(jaccard, lcs_similarity)


def longest_common_subsequence_length(s1: str, s2: str) -> int:
    """Calculate the length of the longest common subsequence."""
    m, n = len(s1), len(s2)
    # Use space-optimized DP
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i - 1] == s2[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, prev

    return prev[n]


def types_compatible(dtype1: str, dtype2: str) -> bool:
    """Check if two types are compatible (same or commonly interchangeable)."""
    if dtype1 == dtype2:
        return True

    # Normalize types
    t1, t2 = dtype1.upper(), dtype2.upper()

    # Numeric types are often interchangeable
    numeric_types = {"INTEGER", "BIGINT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "INT"}
    if t1 in numeric_types and t2 in numeric_types:
        return True

    # String types
    string_types = {"VARCHAR", "TEXT", "STRING", "CHAR"}
    if t1 in string_types and t2 in string_types:
        return True

    # Timestamp/datetime
    time_types = {"TIMESTAMP", "DATETIME", "DATE", "TIME"}
    if t1 in time_types and t2 in time_types:
        return True

    return False
