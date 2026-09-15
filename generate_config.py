import os
import mysql.connector
from mysql.connector import Error

# Database connection configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',               # Replace with your MySQL username
    'password': 'your password',   # Replace with your MySQL password
    'database': 'solar_panel_pipeline'
}

TEMPLATE_PATH = 'inference_google_full.toml'
CONFIG_OUTPUT_DIR = 'configs'

def generate_config_file():
    """Scan for tasks with 'generating_config:pending' statuses, generate tid-specific TOML config file, and update task statuses"""
    connection = None
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        cursor = connection.cursor(dictionary=True)

        # 1. Fetch tasks waiting for config file generation
        fetch_query = "SELECT tid FROM Task WHERE status = 'generating_config:pending';"
        cursor.execute(fetch_query)
        pending_tasks = cursor.fetchall()

        if not pending_tasks:
            print("No tasks currently pending configuration generation.")
            return

        # Ensure output directory exists
        os.makedirs(CONFIG_OUTPUT_DIR, exist_ok=True)

        # Read template file content
        if not os.path.exists(TEMPLATE_PATH):
            raise FileNotFoundError(f"Template configuration file '{TEMPLATE_PATH}' not found.")

        with open(TEMPLATE_PATH, 'r') as file:
            template_content = file.read()

        for task in pending_tasks:
            tid = task['tid']
            print(f"Processing Task ID: {tid}")

            # 2. Update status to running
            update_running_step = """
                UPDATE Task_Step
                SET status = 'running', started_at = CURRENT_TIMESTAMP
                WHERE tid = %s AND step_name = 'generating_config';
            """
            update_running_task = "UPDATE Task SET status = 'generating_config:running' WHERE tid = %s;"

            cursor.execute(update_running_step, (tid,))
            cursor.execute(update_running_task, (tid,))

            # 3. Inject tid into path structure
            # Customized root inference path and specific folder paths with tid
            customized_config = template_content.replace(
                "inference_path = 'data/inference/'",
                f"inference_path = 'data/inference/{tid}/'"
            ).replace(
                "input_folder = 'input'",
                f"input_folder = 'data/inference/{tid}/input'"
            )

            # 4. Save generated config file
            output_filepath = os.path.join(CONFIG_OUTPUT_DIR, f"config_{tid}.toml")
            with open(output_filepath, 'w') as out_file:
                out_file.write(customized_config)

            print(f"Generated config file at: {output_filepath}")

            # 5. Update status to completed and prepare for step 2 (fetch image)
            update_completed_step = """
                UPDATE Task_Step
                SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                WHERE tid = %s AND step_name = 'generating_config';
            """
            update_completed_task = "UPDATE Task SET status = 'fetch_image:pending' WHERE tid = %s;"

            cursor.execute(update_completed_step, (tid,))
            cursor.execute(update_completed_task, (tid,))
            connection.commit()

            print(f"Task ID {tid} successfully updated to 'fetch_image:pending'.\n")

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
    generate_config_file()