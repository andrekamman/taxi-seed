#!/bin/bash

# Initialize counters
total_bytes=0
total_files=0
skipped_files=0
failed_urls=()
base_url="https://d37ci6vzurychx.cloudfront.net/trip-data/fhvhv_tripdata_"

echo "Processing fhvhv_tripdata files from 2019-02 to 2025-01..."
echo "-------------------------------------------------------"

# Loop through years and months
# Calculate previous month and its year
current_year=$(date +%Y)
current_month=$(date +%-m)  # %-m gives month without leading zero

if [ $current_month -eq 1 ]; then
    prev_month=12
    prev_year=$((current_year - 1))
else
    prev_month=$((current_month - 1))
    prev_year=$current_year
fi

for year in $(seq 2019 $prev_year); do
    # Determine end month based on year
    if [ $year -eq $prev_year ]; then
        end_month=$prev_month
    else
        end_month=12
    fi
    
    # ... rest of your loop
done

    # Determine start month based on year
    if [ $year -eq 2019 ]; then
        start_month=2
    else
        start_month=1
    fi

    # Loop through months
    for month in $(seq -f "%02g" $start_month $end_month); do
        file_name="fhvhv_tripdata_${year}-${month}.parquet"
        url="${base_url}${year}-${month}.parquet"

        # Skip if file already exists
        if [ -f "$file_name" ]; then
            echo "Skipping $file_name (already exists)"
            ((skipped_files++))
            continue
        fi

        # Get file size using curl with HEAD request
        size=$(curl -sI "$url" | grep -i "content-length" | awk '{print $2}' | tr -d '\r')

        # Check if size was obtained
        if [ -z "$size" ]; then
            echo "Failed to get size for $url"
            failed_urls+=("$url")
            continue
        fi

        # Calculate size in MB for display
        size_mb=$(echo "scale=2; $size/1024/1024" | bc)

        # Add to totals
        total_bytes=$((total_bytes + size))
        ((total_files++))

        echo "Downloading $file_name ($size_mb MB)..."

        # Download the file
        if curl -s -o "$file_name" "$url"; then
            echo "Successfully downloaded $file_name"
        else
            echo "Failed to download $file_name"
            failed_urls+=("$url")
        fi

        echo "-------------------------------------------------------"
    done
done

# Convert total to human-readable format
total_gb=$(echo "scale=2; $total_bytes/1024/1024/1024" | bc)

echo "Summary:"
echo "-------------------------------------------------------"
echo "Files downloaded this run: $total_files"
echo "Files skipped (already existed): $skipped_files"
echo "Total size downloaded: $total_gb GB"

if [ ${#failed_urls[@]} -gt 0 ]; then
    echo "-------------------------------------------------------"
    echo "Failed to process the following URLs (${#failed_urls[@]} files):"
    for url in "${failed_urls[@]}"; do
        echo "- $url"
    done
fi