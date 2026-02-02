#!/bin/bash

# NYC TLC Trip Data Downloader
# Downloads Yellow, Green, FHV, and FHVHV taxi trip data

# Output directory
output_dir="raw"
urls_file="raw_data_urls.txt"

# Create output directory if it doesn't exist
mkdir -p "$output_dir"

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

# Generate URL list (skipping files that already exist)
generate_urls() {
    local data_type=$1
    local start_year=$2
    local start_month=$3

    for year in $(seq $start_year $prev_year); do
        if [ $year -eq $prev_year ]; then
            end_month=$prev_month
        else
            end_month=12
        fi

        if [ $year -eq $start_year ]; then
            month_start=$start_month
        else
            month_start=1
        fi

        for month in $(seq $month_start $end_month); do
            month_padded=$(printf '%02d' $month)
            filename="${data_type}_tripdata_${year}-${month_padded}.parquet"
            target_path="${output_dir}/${data_type}/${year}/${filename}"

            # Skip if file already exists in organized structure
            if [ -f "$target_path" ]; then
                continue
            fi

            echo "https://d37ci6vzurychx.cloudfront.net/trip-data/${filename}"
        done
    done
}

# Function to organize downloaded files into subdirectories
organize_files() {
    for file in "$output_dir"/*.parquet; do
        [ -e "$file" ] || return  # No files to organize

        filename=$(basename "$file")

        if [[ $filename =~ ^([a-z]+)_tripdata_([0-9]{4})-([0-9]{2})\.parquet$ ]]; then
            data_type="${BASH_REMATCH[1]}"
            year="${BASH_REMATCH[2]}"

            target_dir="$output_dir/$data_type/$year"
            mkdir -p "$target_dir"

            if [ ! -f "$target_dir/$filename" ]; then
                mv "$file" "$target_dir/"
                echo "  Organized: $filename -> $data_type/$year/"
            else
                rm "$file"
            fi
        fi
    done
}

# Generate URLs for all data types
echo "Generating URL list..."
{
    generate_urls "yellow" 2009 1
    generate_urls "green" 2013 8
    generate_urls "fhv" 2015 1
    generate_urls "fhvhv" 2019 2
} > "$urls_file"

total_urls=$(wc -l < "$urls_file")
echo "Files to download: $total_urls (existing files skipped)"
echo ""

if [ "$total_urls" -eq 0 ]; then
    echo "All files already downloaded!"
    rm -f "$urls_file"
    exit 0
fi

echo "Downloading to: ${output_dir}/<type>/<year>/"
echo "Using wget with 2-second delay between downloads..."
echo ""

consecutive_failures=0
rate_limit_pause=300  # 5 minutes

# Download files one by one
while IFS= read -r url; do
    filename=$(basename "$url")

    # Extract type and year for target path
    if [[ $filename =~ ^([a-z]+)_tripdata_([0-9]{4})-([0-9]{2})\.parquet$ ]]; then
        data_type="${BASH_REMATCH[1]}"
        year="${BASH_REMATCH[2]}"
        target_dir="$output_dir/$data_type/$year"
        target_path="$target_dir/$filename"

        # Skip if already exists
        if [ -f "$target_path" ]; then
            echo "Skipping $filename (already exists)"
            continue
        fi

        mkdir -p "$target_dir"

        echo "Downloading $filename..."

        # Download with progress bar
        if wget --progress=bar:force -O "$target_path" "$url" 2>&1; then
            # Verify it's a valid parquet file
            if [ -s "$target_path" ] && head -c 4 "$target_path" 2>/dev/null | grep -q "PAR1"; then
                echo "  Saved to $data_type/$year/$filename"
                consecutive_failures=0
            else
                echo "  Invalid file (not parquet), removing"
                rm -f "$target_path"
                ((consecutive_failures++))
            fi
        else
            echo "  Failed to download $filename"
            rm -f "$target_path"
            ((consecutive_failures++))

            # Check for rate limiting (3 consecutive failures)
            if [ $consecutive_failures -ge 3 ]; then
                echo ""
                echo "=============================================="
                echo "Detected possible rate limiting ($consecutive_failures consecutive failures)"
                echo "Pausing for $((rate_limit_pause / 60)) minutes..."
                echo "Time now: $(date)"
                echo "Will resume at: $(date -d "+${rate_limit_pause} seconds")"
                echo "=============================================="
                echo ""
                sleep $rate_limit_pause
                consecutive_failures=0
                echo "Resuming downloads..."
            fi
        fi

        # Wait between downloads
        sleep 2
    fi
done < "$urls_file"

# Clean up
rm -f "$urls_file"

echo ""
echo "Download complete!"
echo "Files saved to: ${output_dir}/<type>/<year>/"
