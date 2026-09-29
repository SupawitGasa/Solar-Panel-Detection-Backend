import os
import mysql.connector
from mysql.connector import Error

import io
import math
import time
import tomli as tomllib
from pathlib import Path

import numpy as np
import rasterio
import requests
from PIL import Image
from rasterio.transform import from_bounds

# Database connection configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',          # Replace with your MySQL username
    'password': 'my password*', # Replace with your MySQL password
    'database': 'solar_panel_pipeline'
}

# Necessary parameters defined here:
ZOOM = 20
TILE_SIZE = 256
TILE_URL_TEMPLATE = "https://mt{server}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}"
REQUEST_DELAY_SEC = 0.3
REQUEST_TIMEOUT_SEC = 10
CONFIG_OUTPUT_DIR = 'configs'

# The function for converting latitude & longitude <--> tile (Standard formula of Slippy Map / Web Mercator)
def lnglat_to_tile(lng, lat, zoom):
    # Convert latitude & longitude (degrees) into tile location x, y without rounding decimal points
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = (lng + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y

def tile_to_lnglat(x, y, zoom):
    # Convert tile location x, y into latitude & longitude (degrees)
    # For calculating the actual boundaries of the merged image
    n = 2.0 ** zoom
    lng = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat = math.degrees(lat_rad)
    return lng, lat

def lnglat_to_webmercator(lng, lat):
    # Convert latitude & longitude (degrees) into meters in EPSG:3857 (Web Mercator)
    # Which is the coordinate system Google tile naturally uses
    original_shift = 20037508.34
    x = lng * original_shift / 180.0
    y = math.log(math.tan((90 + lat) * math.pi / 360.0)) / (math.pi / 180.0)
    y = y * original_shift / 180.0
    return x, y

# The function for retrieving each tile
def fetch_tile(x, y, zoom, server=0):
    # Download 1 tile & return as a PIL Image
    url = TILE_URL_TEMPLATE.format(server=server, x=x, y=y, z=zoom)
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SEC)
    response.raise_for_status()
    return Image.open(io.BytesIO(response.content)).convert("RGB")

# The pipeline function for fetching an image
def fetch_image():
    # Try to connect with the database
    connection = None

    try:

        connection = mysql.connector.connect(**DB_CONFIG)
        cursor = connection.cursor(dictionary=True)

        # 1. Fetch tasks waiting for fetching images
        # Scan for tasks with "fetch_image:pending" status
        fetch_query = """
            SELECT tid, title, bbox_min_lat, bbox_min_lng, bbox_max_lat, bbox_max_lng 
            FROM Task 
            WHERE status = 'fetch_image:pending';
        """
        cursor.execute(fetch_query)
        pending_tasks = cursor.fetchall()

        # Scan for tasks with "fetch_image:failed" status
        fetch_query = """
            SELECT tid, title, bbox_min_lat, bbox_min_lng, bbox_max_lat, bbox_max_lng 
            FROM Task 
            WHERE status = 'fetch_image:failed';
        """
        cursor.execute(fetch_query)
        failed_tasks = cursor.fetchall()

        # If there are no tasks, the function stops working
        if not(pending_tasks or failed_tasks):
            print("No tasks currently waiting for fetching images.")
            return

        # Otherwise, the function keeps working

        # 2. Fetch images for waiting tasks
        # 2.1 for pending tasks
        if pending_tasks:
            for task in pending_tasks:
                tid = task['tid']
                title = task['title']
                print(f"Processing Task ID: {tid}")
                print(f"Processing Task Title: {title}")

                # Retrieve bounding box locations: min_lat, min_lng, max_lat, max_lng
                min_lat = task['bbox_min_lat']
                min_lng = task['bbox_min_lng']
                max_lat = task['bbox_max_lat']
                max_lng = task['bbox_max_lng']

                BBOX = (min_lng, min_lat, max_lng, max_lat)

                try:

                    # Update status to running for Task and Task_Step
                    update_running_step = """
                        UPDATE Task_Step
                        SET status = 'running', started_at = CURRENT_TIMESTAMP
                        WHERE tid = %s AND step_name = 'fetch_image';
                    """
                    cursor.execute(update_running_step, (tid,))

                    update_running_task = """
                        UPDATE Task
                        SET status = 'fetch_image:running'
                        WHERE tid = %s;
                    """
                    cursor.execute(update_running_task, (tid,))

                    # Verify the coordinates
                    if min_lat >= max_lat or min_lng >= max_lng:
                        raise ValueError(f"Invalid BBOX: lat {min_lat}-{max_lat}, lng {min_lng}-{max_lng}")

                    # Retrieve the input folder path
                    config_file_path = os.path.join(CONFIG_OUTPUT_DIR, f"config_{tid}.toml")
                    with open(config_file_path, "rb") as file:
                        cfg = tomllib.load(file)["inference"]

                    input_folder_path = Path(os.path.join(cfg["inference_path"], cfg["input_folder"]))

                    # Fetch a GeoTIFF image
                    # A: Find which tile indices cover the desired region
                    x_min_f, y_min_f = lnglat_to_tile(min_lng, max_lat, ZOOM) # the top-left corner of the region
                    x_max_f, y_max_f = lnglat_to_tile(max_lng, min_lat, ZOOM) # the bottom-right corner of the region

                    x_start, x_end = int(math.floor(x_min_f)), int(math.floor(x_max_f))
                    y_start, y_end = int(math.floor(y_min_f)), int(math.floor(y_max_f))

                    n_cols = x_end - x_start + 1
                    n_rows = y_end - y_start + 1

                    # B: Create an empty canvas to put tiles on
                    mosaic = Image.new("RGB", (n_cols * TILE_SIZE, n_rows * TILE_SIZE))

                    server_cycle = 0
                    for row, y in enumerate(range(y_start, y_end + 1)):
                        for col, x in enumerate(range(x_start, x_end + 1)):
                            tile_img = fetch_tile(x, y, ZOOM, server=server_cycle % 4) # swap usages of mt0 - mt3
                            mosaic.paste(tile_img, (col * TILE_SIZE, row * TILE_SIZE))
                            server_cycle += 1
                            time.sleep(REQUEST_DELAY_SEC) # lower frequency of request dispatchment

                    # C: Calculate the geographical boundaries of the merged image
                    # top-left corner: (x_start, y_start), bottom-right: (x_end + 1, y_end + 1)
                    # make the boundaries fit with the mosaic boundaries
                    lng_left, lat_top = tile_to_lnglat(x_start, y_start, ZOOM)
                    lng_right, lat_bottom = tile_to_lnglat(x_end + 1, y_end + 1, ZOOM)

                    left_m, top_m = lnglat_to_webmercator(lng_left, lat_top)
                    right_m, bottom_m = lnglat_to_webmercator(lng_right, lat_bottom)

                    # D: Write a mosaic image to a GeoTIFF image
                    # Create an input folder if not exists
                    input_folder_path.mkdir(parents=True, exist_ok=True)

                    # Rasterio requires an array of (bands, height, width) format, not (height, width, bands) format
                    mosaic_array = np.array(mosaic).transpose(2, 0, 1)

                    transform = from_bounds(
                        left_m, bottom_m, right_m, top_m,
                        mosaic.width, mosaic.height
                    )

                    # Save a GeoTIFF image
                    image_path = input_folder_path / f"{title}.tif"
                    with rasterio.open(
                        image_path,
                        "w",
                        driver="GTiff",
                        height=mosaic.height,
                        width=mosaic.width,
                        count=3, #RGB
                        dtype=mosaic_array.dtype,
                        crs="EPSG:3857", # Web Mercator
                        transform=transform
                    ) as dst:
                        dst.write(mosaic_array)

                    print(f"Saved GeoTIFF image at: {image_path}")

                    # Update status to completed for Task_Step and pending for the next task step
                    update_completed_step = """
                        UPDATE Task_Step
                        SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                        WHERE tid = %s AND step_name = 'fetch_image';
                    """
                    cursor.execute(update_completed_step, (tid,))

                    update_completed_task = """
                        UPDATE Task
                        SET status = 'run_inference:pending'
                        WHERE tid = %s;
                    """
                    cursor.execute(update_completed_task, (tid,))

                    # Commit changes to the database
                    connection.commit()
                    print(f"Task ID {tid} status updated to 'run_inference:pending'.\n")

                except Exception as task_err:
                    # If failed, throw away the draft
                    connection.rollback()
                    # Show the error message
                    error_message = str(task_err)
                    print(f"Failed to fetch image for Task ID {tid}")

                    # Update task step
                    fail_step_sql = """
                        UPDATE Task_Step
                        SET status = 'failed', error_msg = %s, completed_at = CURRENT_TIMESTAMP
                        WHERE tid = %s AND step_name = 'fetch_image';
                    """
                    cursor.execute(fail_step_sql, (error_message, tid))
                    # Update task status
                    fail_task_sql = """
                        UPDATE Task
                        SET status = 'fetch_image:failed'
                        WHERE tid = %s;
                    """
                    cursor.execute(fail_task_sql, (tid,))

                    # Commit changes to the database
                    connection.commit()
                    print(f"Task ID {tid} status updated to 'fetch_image:failed'.\n")

        # 2.2 for failed tasks
        if failed_tasks:
            for task in failed_tasks:
                tid = task['tid']
                title = task['title']
                print(f"Retrying Task ID: {tid}")
                print(f"Retrying Task Title: {title}")

                # Check if the number of retrial has reached 5
                retry_count_sql = """
                    SELECT retry_count
                    FROM Task_Step
                    WHERE tid = %s AND step_name = 'fetch_image';
                """
                cursor.execute(retry_count_sql, (tid,))
                retry_num = cursor.fetchone()
                if retry_num and retry_num['retry_count'] >= 5:
                    continue

                # Increment the number of retrial by 1
                update_retry_sql = """
                    UPDATE Task_Step
                    SET retry_count = retry_count + 1
                    WHERE tid = %s AND step_name = 'fetch_image';
                """
                cursor.execute(update_retry_sql, (tid,))

                # Retrieve bounding box locations: min_lat, min_lng, max_lat, max_lng
                min_lat = task['bbox_min_lat']
                min_lng = task['bbox_min_lng']
                max_lat = task['bbox_max_lat']
                max_lng = task['bbox_max_lng']

                BBOX = (min_lng, min_lat, max_lng, max_lat)

                try:

                    # Update status to running for Task and Task_Step
                    update_running_step = """
                        UPDATE Task_Step
                        SET status = 'running', started_at = CURRENT_TIMESTAMP
                        WHERE tid = %s AND step_name = 'fetch_image';
                    """
                    cursor.execute(update_running_step, (tid,))

                    update_running_task = """
                        UPDATE Task
                        SET status = 'fetch_image:running'
                        WHERE tid = %s;
                    """
                    cursor.execute(update_running_task, (tid,))

                    # Verify the coordinates
                    if min_lat >= max_lat or min_lng >= max_lng:
                        raise ValueError(f"Invalid BBOX: lat {min_lat}-{max_lat}, lng {min_lng}-{max_lng}")

                    # Retrieve the input folder path
                    config_file_path = os.path.join(CONFIG_OUTPUT_DIR, f"config_{tid}.toml")
                    with open(config_file_path, "rb") as file:
                        cfg = tomllib.load(file)["inference"]

                    input_folder_path = Path(os.path.join(cfg["inference_path"], cfg["input_folder"]))

                    # Fetch a GeoTIFF image
                    # A: Find which tile indices cover the desired region
                    x_min_f, y_min_f = lnglat_to_tile(min_lng, max_lat, ZOOM) # the top-left corner of the region
                    x_max_f, y_max_f = lnglat_to_tile(max_lng, min_lat, ZOOM) # the bottom-right corner of the region

                    x_start, x_end = int(math.floor(x_min_f)), int(math.floor(x_max_f))
                    y_start, y_end = int(math.floor(y_min_f)), int(math.floor(y_max_f))

                    n_cols = x_end - x_start + 1
                    n_rows = y_end - y_start + 1

                    # B: Create an empty canvas to put tiles on
                    mosaic = Image.new("RGB", (n_cols * TILE_SIZE, n_rows * TILE_SIZE))

                    server_cycle = 0
                    for row, y in enumerate(range(y_start, y_end + 1)):
                        for col, x in enumerate(range(x_start, x_end + 1)):
                            tile_img = fetch_tile(x, y, ZOOM, server=server_cycle % 4) # swap usages of mt0 - mt3
                            mosaic.paste(tile_img, (col * TILE_SIZE, row * TILE_SIZE))
                            server_cycle += 1
                            time.sleep(REQUEST_DELAY_SEC) # lower frequency of request dispatchment

                    # C: Calculate the geographical boundaries of the merged image
                    # top-left corner: (x_start, y_start), bottom-right: (x_end + 1, y_end + 1)
                    # make the boundaries fit with the mosaic boundaries
                    lng_left, lat_top = tile_to_lnglat(x_start, y_start, ZOOM)
                    lng_right, lat_bottom = tile_to_lnglat(x_end + 1, y_end + 1, ZOOM)

                    left_m, top_m = lnglat_to_webmercator(lng_left, lat_top)
                    right_m, bottom_m = lnglat_to_webmercator(lng_right, lat_bottom)

                    # D: Write a mosaic image to a GeoTIFF image
                    # Create an input folder if not exists
                    input_folder_path.mkdir(parents=True, exist_ok=True)

                    # Rasterio requires an array of (bands, height, width) format, not (height, width, bands) format
                    mosaic_array = np.array(mosaic).transpose(2, 0, 1)

                    transform = from_bounds(
                        left_m, bottom_m, right_m, top_m,
                        mosaic.width, mosaic.height
                    )

                    # Save a GeoTIFF image
                    image_path = input_folder_path / f"{title}.tif"
                    with rasterio.open(
                        image_path,
                        "w",
                        driver="GTiff",
                        height=mosaic.height,
                        width=mosaic.width,
                        count=3, #RGB
                        dtype=mosaic_array.dtype,
                        crs="EPSG:3857", # Web Mercator
                        transform=transform
                    ) as dst:
                        dst.write(mosaic_array)

                    print(f"Saved GeoTIFF image at: {image_path}")

                    # Update status to completed for Task_Step and pending for the next task step
                    update_completed_step = """
                        UPDATE Task_Step
                        SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                        WHERE tid = %s AND step_name = 'fetch_image';
                    """
                    cursor.execute(update_completed_step, (tid,))

                    update_completed_task = """
                        UPDATE Task
                        SET status = 'run_inference:pending'
                        WHERE tid = %s;
                    """
                    cursor.execute(update_completed_task, (tid,))

                    # Commit changes to the database
                    connection.commit()
                    print(f"Task ID {tid} status updated to 'run_inference:pending'.\n")

                except Exception as task_err:
                    # If failed, throw away the draft
                    connection.rollback()
                    # Show the error message
                    error_message = str(task_err)
                    print(f"Failed to retry for Task ID {tid}")

                    # Update task step
                    fail_step_sql = """
                        UPDATE Task_Step
                        SET status = 'failed', error_msg = %s, completed_at = CURRENT_TIMESTAMP
                        WHERE tid = %s AND step_name = 'fetch_image';
                    """
                    cursor.execute(fail_step_sql, (error_message, tid))
                    # Update task status
                    fail_task_sql = """
                        UPDATE Task
                        SET status = 'fetch_image:failed'
                        WHERE tid = %s;
                    """
                    cursor.execute(fail_task_sql, (tid,))

                    # Commit changes to the database
                    connection.commit()
                    print(f"Task ID {tid} status updated to 'fetch_image:failed'.\n")


    except Error as e:
        if connection:
            connection.rollback()
        print(f"Database error encountered: {e}")
    except Exception as ex:
        print(f"Execution error: {ex}")
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()

if __name__ == "__main__":
    fetch_image()