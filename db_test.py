import mysql.connector
from mysql.connector import Error

# Database connection configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',               # Replace with your MySQL username
    'password': 'my password',   # Replace with your MySQL password
    'database': 'solar_panel_pipeline'
}

def fetch_table_data():
    """Connects to the MySQL database and prints contents of all workflow tables."""
    # List of tables from your database schema
    tables = ['User', 'Task', 'Task_Step', 'Task_Result']
    
    try:
        # Establish connection to the MySQL server
        connection = mysql.connector.connect(**DB_CONFIG)
        
        if connection.is_connected():
            print("Successfully connected to MySQL database.")
            cursor = connection.cursor(dictionary=True)  # Returns rows as dictionary objects
            
            for table in tables:
                print(f"\n=== Data from table: {table} ===")
                cursor.execute(f"SELECT * FROM {table};")
                rows = cursor.fetchall()
                
                if not rows:
                    print(f"No records found in {table}.")
                else:
                    for row in rows:
                        print(row)
                        
    except Error as e:
        print(f"Database error occurred: {e}")
        
    finally:
        # Ensure connection is properly closed
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()
            print("\nMySQL connection closed.")

if __name__ == "__main__":
    fetch_table_data()