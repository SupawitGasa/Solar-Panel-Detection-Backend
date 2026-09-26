import os
import mysql.connector
from mysql.connector import Error

# Database connection configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',          # Replace with your MySQL username
    'password': 'my password', # Replace with your MySQL password
    'database': 'solar_panel_pipeline'
}

# Necessary paths for config file generation
TEMPLATE_PATH  = 'inference_google_full.toml'
CONFIG_OUTPUT_DIR = 'configs'

# The function for generating config file goes here:
def generating_config_file():

    # Try to connect with the database
    connection = None

    try:

        connection = mysql.connector.connect(**DB_CONFIG)
        cursor = connection.cursor(dictionary=True)

        # 1. Fetch tasks waiting for config file generation
        # Scan for tasks with "generating_config:pending" status
        fetch_query = "SELECT tid, title FROM Task WHERE status = 'generating_config:pending';"
        cursor.execute(fetch_query)
        pending_tasks = cursor.fetchall()

        # Scan for tasks with "generating_config:failed" status
        fetch_query = "SELECT tid, title FROM Task WHERE status = 'generating_config:failed';"
        cursor.execute(fetch_query)
        failed_tasks = cursor.fetchall()

        # If there are no tasks, the function then stops working
        if not (pending_tasks or failed_tasks):
            print("No tasks currently waiting for config files generation.")
            return

        # Otherwise, the function keeps working
        # Ensure output directory exists
        os.makedirs(CONFIG_OUTPUT_DIR, exist_ok=True)

        # Read the config file template content
        if not os.path.exists(TEMPLATE_PATH):
            raise FileNotFoundError(f"Template configuration file '{TEMPLATE_PATH}' not found.")

        with open(TEMPLATE_PATH, 'r') as file:
            template_content = file.read()

        # 2. Generate config files for waiting tasks
        # 2.1 Generate config files for pending tasks
        if pending_tasks:
            for task in pending_tasks:
                tid = task['tid']
                title = task['title']
                print(f"Processing Task ID: {tid}")
                print(f"Processing Task Title: {title}")

                try:
                    # Update status to running for Task and Task_Step
                    update_running_step = """
                        UPDATE Task_Step
                        SET status = 'running', started_at = CURRENT_TIMESTAMP
                        WHERE tid = %s AND step_name = 'generating_config';
                    """
                    cursor.execute(update_running_step, (tid,))

                    update_running_task = """
                        UPDATE Task
                        SET status = 'generating_config:running'
                        WHERE tid = %s;
                    """
                    cursor.execute(update_running_task, (tid,))

                    # Generating config file according to the template
                    customized_config = template_content.replace(
                        "Inference_path = 'data/inference/'",
                        f"inference_path = 'data/inference/{tid}/'"
                    ).replace(
                        "job_name = 'job_google_full'",
                        f"job_name = '{title}'"
                    )

                    # Save generated config file
                    output_filepath = os.path.join(CONFIG_OUTPUT_DIR, f"config_{tid}.toml")
                    with open(output_filepath, 'w') as out_file:
                        out_file.write(customized_config)

                    print(f"Generated config file at: {output_filepath}")

                    # Update status to completed for Task_Step and pending for the next task
                    update_completed_step = """
                        UPDATE Task_Step
                        SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                        WHERE tid = %s AND step_name = 'generating_config';
                    """ 
                    cursor.execute(update_completed_step, (tid,))

                    update_completed_task = """
                        UPDATE Task
                        SET status = 'fetch_image:pending'
                        WHERE tid = %s;
                    """
                    cursor.execute(update_completed_task, (tid,))

                    # Commit changes to the database
                    connection.commit()

                except Exception as task_err:
                    # If failed, throw away the draft
                    connection.rollback()
                    # Show the error message
                    error_message = str(task_err)
                    print(f"Failed to generate config file for Task ID {tid}: {error_message}.")

                    # Update task step
                    fail_step_sql = """
                        UPDATE Task_Step
                        SET status = 'failed', error_msg = %s, completed_at = CURRENT_TIMESTAMP
                        WHERE tid = %s AND step_name = 'generating_config';
                    """
                    cursor.execute(fail_step_sql, (error_message, tid))
                    # Update task status
                    fail_task_sql = """
                        UPDATE Task
                        SET status = 'generating_config:failed'
                        WHERE tid = %s;
                    """
                    cursor.execute(fail_task_sql, (tid,))

                    # Commit changes to the database
                    connection.commit()
                    print(f"Task ID {tid} status updated to 'generating_config:failed'.\n")

        # 2.2 Generate config files for pending tasks
        if failed_tasks:
            for task in failed_tasks:
                tid = task['tid']
                title = task['title']
                print(f"Retrying Task ID: {tid}")
                print(f"Retry Task Title: {title}")

                # Increment the number of retrial by 1
                update_retry_sql = """
                    UPDATE Task_Step
                    SET retry_count = retry_count + 1
                    WHERE tid = %s AND step_name = 'generating_config'; 
                """
                cursor.execute(update_retry_sql, (tid,))

                try:
                    # Update status to running for Task and Task_Step
                    update_running_step = """
                        UPDATE Task_Step
                        SET status = 'running', started_at = CURRENT_TIMESTAMP
                        WHERE tid = %s AND step_name = 'generating_config';
                    """
                    cursor.execute(update_running_step, (tid,))

                    update_running_task = """
                        UPDATE Task
                        SET status = 'generating_config:running'
                        WHERE tid = %s;
                    """
                    cursor.execute(update_running_task, (tid,))

                    # Generating config file according to the template
                    customized_config = template_content.replace(
                        "Inference_path = 'data/inference/'",
                        f"inference_path = 'data/inference/{tid}/'"
                    ).replace(
                        "job_name = 'job_google_full'",
                        f"job_name = '{title}'"
                    )

                    # Save generated config file
                    output_filepath = os.path.join(CONFIG_OUTPUT_DIR, f"config_{tid}.toml")
                    with open(output_filepath, 'w') as out_file:
                        out_file.write(customized_config)

                    print(f"Generated config file at: {output_filepath}")

                    # Update status to completed for Task_Step and pending for the next task
                    update_completed_step = """
                        UPDATE Task_Step
                        SET status = 'completed', completed_at = CURRENT_TIMESTAMP, error_msg = NULL
                        WHERE tid = %s AND step_name = 'generating_config';
                    """ 
                    cursor.execute(update_completed_step, (tid,))

                    update_completed_task = """
                        UPDATE Task
                        SET status = 'fetch_image:pending'
                        WHERE tid = %s;
                    """
                    cursor.execute(update_completed_task, (tid,))

                    # Commit changes to the database
                    connection.commit()
                    
                except Exception as task_err:
                    # If failed, throw away the draft
                    connection.rollback()
                    # Show the error message
                    error_message = str(task_err)
                    print(f"Failed to retry for Task ID {tid}: {error_message}")

                    # Update task step
                    fail_step_sql = """
                        UPDATE Task_Step
                        SET status = 'failed', error_msg = %s, completed_at = CURRENT_TIMESTAMP
                        WHERE tid = %s AND step_name = 'generating_config';
                    """
                    cursor.execute(fail_step_sql, (error_message, tid))

                    # Update task status
                    fail_task_sql = """
                        UPDATE Task
                        SET status = 'generating_config:failed'
                        WHERE tid = %s;
                    """
                    cursor.execute(fail_task_sql, (tid,))

                    # Commit changes to the database
                    connection.commit()
                    print(f"Task ID {tid} status updated to 'generating_config:failed'.\n")

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
    generating_config_file()