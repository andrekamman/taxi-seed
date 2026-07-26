#!/bin/bash

# NYC TLC Trip Data Downloader
# Downloads Yellow, Green, FHV, and FHVHV taxi trip data

# Parse flags
recent_only=0
recent_months=3
max_lookback_months=18  # in --recent mode, how far back to look per type before giving up
recent_type=""          # empty = all four types
full_type=""            # empty = all four types (full-history mode)
data_dir_opt=""         # base dir; when set, output_dir = $data_dir_opt/raw

DATA_TYPES=("yellow" "green" "fhv" "fhvhv")

is_data_type() {
    local candidate="$1"
    for t in "${DATA_TYPES[@]}"; do
        [[ "$candidate" == "$t" ]] && return 0
    done
    return 1
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --recent)
            recent_only=1
            shift
            # Optional numeric N
            if [[ $# -gt 0 && "$1" =~ ^[0-9]+$ ]]; then
                recent_months=$1
                shift
            fi
            # Optional TYPE
            if [[ $# -gt 0 ]] && is_data_type "$1"; then
                recent_type=$1
                shift
            fi
            ;;
        --data-dir)
            shift
            if [[ $# -gt 0 ]]; then
                data_dir_opt="$1"
                shift
            else
                echo "--data-dir requires a directory argument" >&2
                exit 1
            fi
            ;;
        -h|--help)
            cat <<'HELP'
Usage:
  ./download_taxi_data.sh                          Full history, all four types
  ./download_taxi_data.sh TYPE                     Full history, one type only
  ./download_taxi_data.sh --recent [N]             Recent N months (default 3), all types
  ./download_taxi_data.sh --recent [N] TYPE        Recent N months (default 3), one type only
  ./download_taxi_data.sh --recent TYPE            Recent 3 months, one type only
  ./download_taxi_data.sh --data-dir DIR            Write to DIR/raw (instead of ./raw)

TYPE is one of: yellow, green, fhv, fhvhv.

Recent-mode walker semantics:
  Walks backward from the previous month. A remotely-not-yet-published month
  is skipped without counting. A locally-already-existing file stops the walker
  (assumes prior runs downloaded everything older). Downloads count toward N.
HELP
            exit 0
            ;;
        *)
            if is_data_type "$1"; then
                full_type=$1
                shift
            else
                echo "Unknown option: $1" >&2
                echo "Run '$0 --help' for usage." >&2
                exit 1
            fi
            ;;
    esac
done

# Output directory precedence: OUTPUT_DIR (explicit full path) > --data-dir/raw
# > raw/ resolved relative to this script (so it works from any CWD).
if [ -n "$OUTPUT_DIR" ]; then
    output_dir="$OUTPUT_DIR"
elif [ -n "$data_dir_opt" ]; then
    output_dir="$data_dir_opt/raw"
else
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    output_dir="$script_dir/../raw"
fi

# Warn if running under WSL and about to write to the Linux filesystem. The
# TLC dataset is large (100+ GB full history) and everything under the WSL2
# root grows the VHDX file on the Windows C: drive — which does not shrink
# when you later delete files.
is_wsl() { grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; }
if is_wsl && [[ "$output_dir" != /mnt/* ]]; then
    echo "WARNING: WSL detected — about to write to the Linux filesystem."
    echo "  Path:      $output_dir"
    echo "  Downloads: up to ~100 GB (full history), ~1 GB (--recent 3)"
    echo "  Impact:    grows your WSL2 VHDX on the Windows C: drive."
    echo ""
    echo "  To write to a Windows path instead, set OUTPUT_DIR, e.g.:"
    echo "    OUTPUT_DIR=/mnt/c/Users/\$USER/taxi-data $0 $*"
    echo ""
    if [ -t 0 ]; then
        read -p "Continue writing to $output_dir? [y/N] " reply
        [[ "$reply" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }
    else
        echo "  (non-interactive: proceeding without confirmation)"
    fi
    echo ""
fi

# Create output directory if it doesn't exist
mkdir -p "$output_dir"

# Function to remove corrupt parquet files (missing PAR1 magic bytes)
# Parquet files must have PAR1 at both the start AND end of the file
cleanup_corrupt_files() {
    echo "Checking for corrupt parquet files..."
    local corrupt_count=0

    while IFS= read -r -d '' file; do
        if [ -f "$file" ]; then
            local is_corrupt=0

            # Check for PAR1 magic bytes at start of file
            if ! head -c 4 "$file" 2>/dev/null | grep -q "PAR1"; then
                is_corrupt=1
            # Check for PAR1 magic bytes at end of file (truncation check)
            elif ! tail -c 4 "$file" 2>/dev/null | grep -q "PAR1"; then
                is_corrupt=1
            fi

            if [ $is_corrupt -eq 1 ]; then
                echo "  Removing corrupt file: $file"
                rm -f "$file"
                ((corrupt_count++))
            fi
        fi
    done < <(find "$output_dir" -name "*.parquet" -print0 2>/dev/null)

    if [ $corrupt_count -gt 0 ]; then
        echo "  Removed $corrupt_count corrupt file(s)"
    else
        echo "  No corrupt files found"
    fi
    echo ""
}

# Clean up any corrupt files before generating URL list
cleanup_corrupt_files

# ------------------------------------------------------------------
# HTTP classification + rate-limit handling
# ------------------------------------------------------------------
#
# CloudFront responds in a few distinguishable ways:
#   - existing file:  HTTP 206 / 200, body begins with PAR1
#   - not published:  HTTP 403 with an S3-style <Code>AccessDenied</Code> XML body
#   - WAF/rate block: HTTP 403 with an HTML "The request could not be satisfied"
#                     page, or HTTP 429 / 503 directly
#
# We MUST distinguish these — hammering a WAF block extends it, and treating a
# genuine "file not published yet" as a rate limit stalls the whole run.

# Classifies one URL. Echoes one of: ok | notfound | ratelimit | neterror
probe_url() {
    local url=$1
    local body_file
    body_file=$(mktemp -t tlc_probe.XXXXXX)
    local http_code
    http_code=$(curl -sS -o "$body_file" -w "%{http_code}" \
                    --max-time 30 --range 0-4095 "$url" 2>/dev/null)
    local curl_exit=$?

    local result
    if [ $curl_exit -ne 0 ]; then
        # Connection reset / timeout — be conservative and back off, don't pound.
        result="ratelimit"
    else
        case "$http_code" in
            200|206)
                if head -c 4 "$body_file" 2>/dev/null | grep -q "PAR1"; then
                    result="ok"
                else
                    # 200 but not parquet — probably an intercept page. Treat as
                    # a WAF-style block rather than a missing file.
                    result="ratelimit"
                fi
                ;;
            403)
                if grep -qi "AccessDenied\|NoSuchKey" "$body_file" 2>/dev/null; then
                    result="notfound"
                elif grep -qi "could not be satisfied\|generated by cloudfront\|blocked" \
                          "$body_file" 2>/dev/null; then
                    result="ratelimit"
                else
                    # Ambiguous 403 with no obvious body — safer to skip than to
                    # back off forever.
                    result="notfound"
                fi
                ;;
            404)
                result="notfound"
                ;;
            429|502|503|504)
                result="ratelimit"
                ;;
            *)
                result="neterror"
                ;;
        esac
    fi

    rm -f "$body_file"
    echo "$result"
}

# Exponential backoff state — reset to 0 on every successful download.
ratelimit_hits=0
ratelimit_max=3  # 5min, 15min, 60min, then abort

# Called when probe_url returns "ratelimit". Sleeps with escalating backoff.
# Returns 0 if the caller should retry, 1 if we've given up.
handle_rate_limit() {
    ratelimit_hits=$((ratelimit_hits + 1))
    local pause
    case $ratelimit_hits in
        1) pause=300 ;;    # 5 minutes
        2) pause=900 ;;    # 15 minutes
        3) pause=3600 ;;   # 60 minutes
        *)
            echo "ERROR: rate-limited $ratelimit_hits times in a row. Aborting." >&2
            return 1
            ;;
    esac
    echo ""
    echo "=============================================="
    echo "Rate limit / WAF block detected (backoff #$ratelimit_hits)."
    echo "Pausing for $((pause / 60)) minute(s)."
    echo "Time now: $(date)"
    echo "=============================================="
    echo ""
    sleep "$pause"
    return 0
}

# Download one URL to a target path. Uses probe_url first to avoid triggering
# WAF blocks with a full GET on a missing file.
# Echoes one of: ok | notfound | ratelimit | neterror
# On "ok" the file at target_path is a verified parquet.
download_one() {
    local url=$1
    local target_path=$2
    local target_dir
    target_dir=$(dirname "$target_path")
    mkdir -p "$target_dir"

    local status
    status=$(probe_url "$url")

    if [ "$status" != "ok" ]; then
        echo "$status"
        return
    fi

    # Probe said ok — do the real download.
    if curl -fSL --progress-bar -o "$target_path" "$url"; then
        if [ -s "$target_path" ] && head -c 4 "$target_path" 2>/dev/null | grep -q "PAR1" \
                                 && tail -c 4 "$target_path" 2>/dev/null | grep -q "PAR1"; then
            echo "ok"
            return
        fi
        # Download succeeded per curl but the bytes aren't a valid parquet.
        # Probably a WAF intercept mid-transfer.
        rm -f "$target_path"
        echo "ratelimit"
        return
    fi

    rm -f "$target_path"
    echo "neterror"
}

# Calculate previous month (data is typically available with 1 month lag)
current_year=$(date +%Y)
current_month=$(date +%-m)

if [ $current_month -eq 1 ]; then
    prev_month=12
    prev_year=$((current_year - 1))
else
    prev_month=$((current_month - 1))
    prev_year=$current_year
fi

echo "NYC TLC Trip Data Downloader"
echo "Generating URL list up to: ${prev_year}-$(printf '%02d' $prev_month)"
echo ""

# Walk one data type chronologically forward from (start_year, start_month)
# through prev_year/prev_month. Downloads any missing months.
#
# "notfound" handling depends on where we are in the series:
#   - before the first hit: skip forward (data may just start later than the
#     hard-coded start month)
#   - after the first hit: assume we've walked past the last published month
#     and stop — the TLC series is contiguous in practice
download_full_type() {
    local data_type=$1
    local year=$2
    local month=$3
    local have_seen_data=0
    local downloaded=0

    echo "--- $data_type: catching up from $year-$(printf '%02d' $month) ---"

    while [ $year -lt $prev_year ] || \
          { [ $year -eq $prev_year ] && [ $month -le $prev_month ]; }; do

        local month_padded
        month_padded=$(printf '%02d' $month)
        local filename="${data_type}_tripdata_${year}-${month_padded}.parquet"
        local target_path="$output_dir/$data_type/$year/$filename"

        if [ -f "$target_path" ]; then
            have_seen_data=1
        else
            local url="https://d37ci6vzurychx.cloudfront.net/trip-data/${filename}"
            echo "  $filename"
            local status
            status=$(download_one "$url" "$target_path")
            case "$status" in
                ok)
                    echo "    Saved"
                    have_seen_data=1
                    ((downloaded++))
                    ratelimit_hits=0
                    sleep 2
                    ;;
                notfound)
                    if [ $have_seen_data -eq 1 ]; then
                        echo "    Not published — reached end of $data_type series, moving on"
                        echo "  Downloaded $downloaded new file(s) for $data_type"
                        echo ""
                        return
                    else
                        echo "    Not published (pre-series), skipping forward"
                    fi
                    ;;
                ratelimit)
                    if handle_rate_limit; then
                        # Retry the same month without advancing.
                        continue
                    else
                        return
                    fi
                    ;;
                neterror)
                    echo "    Transient error, skipping"
                    sleep 5
                    ;;
            esac
        fi

        # Advance one month.
        ((month++))
        if [ $month -eq 13 ]; then
            month=1
            ((year++))
        fi
    done

    echo "  Downloaded $downloaded new file(s) for $data_type"
    echo ""
}

# Download recent N months of a data type (newest first). "Not published yet"
# is expected at the head of the range and does not count as a failure — we
# just walk backwards until we've collected N months (or hit the lookback cap).
download_recent_type() {
    local data_type=$1
    local want=$recent_months
    local got=0
    local walked=0
    local year=$prev_year
    local month=$prev_month

    echo "--- $data_type: looking for $want recent months ---"

    while [ $got -lt $want ] && [ $walked -lt $max_lookback_months ]; do
        walked=$((walked + 1))
        month_padded=$(printf '%02d' $month)
        filename="${data_type}_tripdata_${year}-${month_padded}.parquet"
        target_path="$output_dir/$data_type/$year/$filename"

        if [ -f "$target_path" ]; then
            echo "  Already have $filename — stopping (assume prior runs are caught up)"
            break
        fi
        local url="https://d37ci6vzurychx.cloudfront.net/trip-data/${filename}"
        echo "  Trying $filename..."
        local status
        status=$(download_one "$url" "$target_path")

        case "$status" in
            ok)
                echo "    Saved to $data_type/$year/$filename"
                ((got++))
                ratelimit_hits=0
                sleep 2
                ;;
            notfound)
                echo "    Not published yet, trying older month"
                # No sleep — cheap probe, keep walking back.
                ;;
            ratelimit)
                if handle_rate_limit; then
                    # Retry the SAME month after backing off.
                    walked=$((walked - 1))
                else
                    return
                fi
                ;;
            neterror)
                echo "    Transient error, brief pause then continue"
                sleep 10
                ;;
        esac

        # Move to previous month
        ((month--))
        if [ $month -eq 0 ]; then
            month=12
            ((year--))
        fi
    done

    echo "  Downloaded $got new file(s) for $data_type (walked back $walked month(s))"
    echo ""
}

# Recent mode: download inline per type, walking back for unpublished months,
# stopping when a local file is encountered (incremental catch-up).
if [ $recent_only -eq 1 ]; then
    echo "Downloading recent $recent_months months$([ -n "$recent_type" ] && echo " ($recent_type)")..."
    echo "Will walk back through older not-yet-published months, and stop"
    echo "at the first locally-existing file (assumes prior runs are caught up)."
    echo ""

    if [ -n "$recent_type" ]; then
        download_recent_type "$recent_type"
    else
        download_recent_type "yellow"
        download_recent_type "green"
        download_recent_type "fhv"
        download_recent_type "fhvhv"
    fi

    echo "Download complete!"
    echo "Files saved to: ${output_dir}/<type>/<year>/"
    exit 0
fi

# Full mode: catch up on all history. Each walker stops when it hits the
# end of that type's published series and moves on.
echo "Catching up all history$([ -n "$full_type" ] && echo " ($full_type)")..."
echo "Files saved to: ${output_dir}/<type>/<year>/"
echo ""

case "$full_type" in
    "")
        download_full_type "yellow" 2009 1
        download_full_type "green"  2013 8
        download_full_type "fhv"    2015 1
        download_full_type "fhvhv"  2019 2
        ;;
    yellow)  download_full_type "yellow" 2009 1 ;;
    green)   download_full_type "green"  2013 8 ;;
    fhv)     download_full_type "fhv"    2015 1 ;;
    fhvhv)   download_full_type "fhvhv"  2019 2 ;;
esac

echo "Download complete!"
