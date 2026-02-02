#!/bin/bash

# NYC TLC Trip Data Downloader
# Downloads Yellow, Green, FHV, and FHVHV taxi trip data

# Initialize counters
total_bytes=0
total_files=0
skipped_files=0
failed_urls=()

# Base URL for all taxi data
base_url="https://d37ci6vzurychx.cloudfront.net/trip-data"

# Output directory
output_dir="raw"

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

# Function to download a range of files for a specific data type
download_data_type() {
    local data_type=$1
    local start_year=$2
    local start_month=$3

    echo ""
    echo "======================================================="
    echo "Processing ${data_type}_tripdata files from ${start_year}-$(printf '%02d' $start_month) to ${prev_year}-$(printf '%02d' $prev_month)..."
    echo "======================================================="

    for year in $(seq $start_year $prev_year); do
        # Determine end month based on year
        if [ $year -eq $prev_year ]; then
            end_month=$prev_month
        else
            end_month=12
        fi

        # Determine start month based on year
        if [ $year -eq $start_year ]; then
            month_start=$start_month
        else
            month_start=1
        fi

        # Loop through months
        for month in $(seq $month_start $end_month); do
            month_padded=$(printf '%02d' $month)
            file_name="${data_type}_tripdata_${year}-${month_padded}.parquet"
            url="${base_url}/${file_name}"

            # Create subfolder structure: raw/<type>/<year>/
            sub_dir="${output_dir}/${data_type}/${year}"
            mkdir -p "$sub_dir"
            output_path="${sub_dir}/${file_name}"

            # Skip if file already exists
            if [ -f "$output_path" ]; then
                echo "Skipping $file_name (already exists)"
                ((skipped_files++))
                continue
            fi

            # Get file size using curl with HEAD request
            size=$(curl -sI "$url" | grep -i "content-length" | awk '{print $2}' | tr -d '\r')

            # Check if size was obtained (file exists on server)
            if [ -z "$size" ] || [ "$size" -eq 0 ] 2>/dev/null; then
                echo "Skipping $file_name (not available on server)"
                continue
            fi

            # Calculate size in MB for display
            size_mb=$(echo "scale=2; $size/1024/1024" | bc)

            echo "Downloading $file_name ($size_mb MB)..."

            # Download the file
            if curl -s -o "$output_path" "$url"; then
                # Verify file was downloaded successfully
                if [ -s "$output_path" ]; then
                    echo "Successfully downloaded $file_name"
                    total_bytes=$((total_bytes + size))
                    ((total_files++))
                else
                    echo "Failed to download $file_name (empty file)"
                    rm -f "$output_path"
                    failed_urls+=("$url")
                fi
            else
                echo "Failed to download $file_name"
                failed_urls+=("$url")
            fi
        done
    done
}

echo "NYC TLC Trip Data Downloader"
echo "Downloading to: ${output_dir}/"
echo "Data available up to: ${prev_year}-$(printf '%02d' $prev_month)"

# Download all data types with their respective start dates
# Yellow Taxi: Started January 2009
download_data_type "yellow" 2009 1

# Green Taxi: Started August 2013
download_data_type "green" 2013 8

# FHV (For-Hire Vehicle): Started January 2015
download_data_type "fhv" 2015 1

# FHVHV (High Volume For-Hire Vehicle): Started February 2019
download_data_type "fhvhv" 2019 2

# Convert total to human-readable format
total_gb=$(echo "scale=2; $total_bytes/1024/1024/1024" | bc)

echo ""
echo "======================================================="
echo "Summary:"
echo "======================================================="
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
