from flask import Flask, request, jsonify
import random
import mysql.connector
import logging
from ping3 import ping
from concurrent.futures import ThreadPoolExecutor
import time

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# Database configurations
MASTER_DB = {"host": "MANAGER_IP", "user": "root", "password": "password123", "database": "sakila"}
SERVER_DBS = [
    {"host": "MANAGER_IP", "user": "root", "password": "password123", "database": "sakila"},
    {"host": "WORKER1_IP", "user": "root", "password": "password123", "database": "sakila"},
    {"host": "WORKER2_IP", "user": "root", "password": "password123", "database": "sakila"}
]
WORKER_DBS = [
    {"host": "WORKER1_IP", "user": "root", "password": "password123", "database": "sakila"},
    {"host": "WORKER2_IP", "user": "root", "password": "password123", "database": "sakila"}
]

# Benchmark result storage file
BENCHMARK_FILE = "/tmp/cluster_benchmark.txt"

# Store benchmark results
benchmark_all_results = {
    "direct_hit": {
        "total_requests": 0,
        "total_read_requests": 0,
        "total_write_requests": 0,
        "total_time": 0,
        "average_time": 0,
    },
    "random": {
        "total_requests": 0,
        "total_read_requests": 0,
        "total_write_requests": 0,
        "total_time": 0,
        "average_time": 0,
    },
    "customized": {
        "total_requests": 0,
        "total_read_requests": 0,
        "total_write_requests": 0,
        "total_time": 0,
        "average_time": 0,
    },
}

def save_benchmark_to_file():
    """
    Save the current benchmarking results for all modes to a file in a formatted way.
    """
    with open(BENCHMARK_FILE, "w") as file:
        file.write("===== Proxy Benchmark Results =====\n\n")
        for mode, data in benchmark_all_results.items():
            file.write(f"--- Mode: {mode.capitalize()} ---\n")
            file.write(f"Total Requests: {data['total_requests']}\n")
            file.write(f"  - Total Read Requests: {data['total_read_requests']}\n")
            file.write(f"  - Total Write Requests: {data['total_write_requests']}\n")
            file.write(f"Total Time Taken: {data['total_time']:.4f} seconds\n")
            file.write(f"Average Time per Request: {data['average_time']:.4f} seconds\n")
            if data['total_write_requests'] > 0:
                avg_write_time = data['total_time'] / data['total_write_requests']
                file.write(f"  - Average Time per Write Request: {avg_write_time:.4f} seconds\n")
            if data['total_read_requests'] > 0:
                avg_read_time = data['total_time'] / data['total_read_requests']
                file.write(f"  - Average Time per Read Request: {avg_read_time:.4f} seconds\n")
            file.write("\n")

def update_benchmark(mode, query_type, cluster_request_time):
    """
    Update the benchmark statistics for a specific mode and query type.
    """
    data = benchmark_all_results[mode]
    data["total_requests"] += 1
    data["total_time"] += cluster_request_time

    # Update specific counters
    if query_type == "read":
        data["total_read_requests"] += 1
    elif query_type == "write":
        data["total_write_requests"] += 1

    # Recalculate average time
    data["average_time"] = data["total_time"] / data["total_requests"]

    # Save the updated benchmark to the file
    save_benchmark_to_file()





def ping_server(host):
    """
    Ping a server and return its response time in milliseconds.
    Log whether the ping was successful or timed out.
    """
    try:
        response_time = ping(host, timeout=10)  # Timeout set to 10 seconds
        if response_time is not None:
            logging.info(f"Ping successful for {host}: {response_time * 1000:.2f} ms")
            return response_time * 1000  # Convert seconds to milliseconds
        else:
            logging.warning(f"Ping timed out for {host}")
    except Exception as e:
        logging.error(f"Error pinging {host}: {e}")
    return float('inf')  # Return a large value if the server is unreachable


def get_fastest_server():
    """
    Get the fastest server by pinging all servers in parallel.
    """
    def ping_server_wrapper(db):
        """
        Wrapper to ping a server and return the database config with its ping time.
        """
        host = db["host"]
        ping_time = ping_server(host)
        logging.info(f"Ping result for {host}: {ping_time:.2f} ms")
        return (db, ping_time)

    # Use ThreadPoolExecutor to ping all servers in parallel
    with ThreadPoolExecutor() as executor:
        ping_results = list(executor.map(ping_server_wrapper, SERVER_DBS))

    # Find the server with the minimum ping time
    fastest_server = min(ping_results, key=lambda x: x[1])[0]
    logging.info(f"Fastest server selected: {fastest_server['host']}")
    return fastest_server


def execute_query(db_config, query):
    """
    Execute a query on the specified database.
    """
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        logging.info(f"Executing query: {query}")
        cursor.execute(query)

        # Commit changes for non-SELECT queries
        if query.strip().lower().startswith(("insert", "update", "delete")):
            conn.commit()
            result = {"affected_rows": cursor.rowcount}
        else:
            result = cursor.fetchall()

        conn.close()
        logging.info(f"Query executed successfully, result: {result}")
        return result
    except mysql.connector.Error as err:
        logging.error(f"Error: {err}")
        return {"error": str(err)}

@app.route('/query', methods=['POST'])
def route_request():
    """
    Route incoming requests based on the query type and mode.
    """
    data = request.get_json()
    query_type = data.get("type", "read").lower()
    query = data.get("query", "")
    mode = data.get("mode", "random").lower()
    logging.info(f"Received request: type={query_type}, mode={mode}, query={query}")

    server_db = None

    if query_type == "write":
        server_db = MASTER_DB
    elif query_type == "read":
        if mode == "direct_hit":
            server_db = MASTER_DB
        elif mode == "random":
            server_db = random.choice(WORKER_DBS)
            logging.info(f"Randomly selected server database: {server_db['host']}")
        elif mode == "customized":
            server_db = get_fastest_server()
            logging.info(f"Selected server database based on ping: {server_db['host']}")
        else:
            return jsonify({"error": "Invalid mode"}), 400
    else:
        return jsonify({"error": "Invalid query type"}), 400

    cluster_start_time = time.time()

    # Execute the query
    result = execute_query(server_db, query)

    cluster_end_time = time.time()
    cluster_request_time = cluster_end_time - cluster_start_time

    # Update benchmarking metrics
    update_benchmark(mode, query_type, cluster_request_time)

    logging.info(f"Cluster {server_db['host']} request time: {cluster_request_time:.4f} seconds")
    return jsonify({
        "result": result,
        "cluster_time_taken": f"{cluster_request_time:.4f} seconds"
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
