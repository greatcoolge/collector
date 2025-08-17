from concurrent.futures import ThreadPoolExecutor, as_completed
import yaml
import time
import requests
import socket
import logging
import os

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

LATENCY_THRESHOLD = 30  # 延迟阈值（毫秒）
MIN_LATENCY_THRESHOLD = 1  # 低于此阈值的代理节点将被移除（毫秒）
URL_FILE_OR_FILES = ['./urls/clash.txt', './urls/xxx.txt']  # 支持一个或多个 URL 文件
MERGED_PROXIES_FILE = './sub/merged_proxies.yaml'

def process_urls(url_files, processor):
    """从文件中读取 URL 列表，并并发处理每个 URL 的内容"""
    if isinstance(url_files, str):
        url_files = [url_files]  # 如果是单个文件，转换为列表

    urls = []
    try:
        for url_file in url_files:
            if not os.path.exists(url_file):
                logging.error(f"File not found: {url_file}")
                continue  # 跳过文件不存在的情况

            with open(url_file, 'r') as file:
                urls += file.read().splitlines()

        # 去除空行和注释行
        urls = [url.strip() for url in urls if url.strip() and not url.startswith('#')]
        logging.info(f"Processing {len(urls)} URLs")

        # 使用线程池并发处理 URL
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(fetch_and_process_url, url, processor, index) for index, url in enumerate(urls)]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logging.error(f"Error in URL processing: {e}")

    except Exception as e:
        logging.error(f"Error processing URLs: {e}")

def fetch_and_process_url(url, processor, index):
    """获取 URL 内容并处理"""
    try:
        logging.info(f"Fetching URL: {url}")
        response = requests.get(url)
        response.raise_for_status()
        data = response.text
        processor(data, index)
    except requests.RequestException as e:
        logging.error(f"Error processing URL {url}: {e}")

def process_clash(data, index):
    """处理 Clash 配置文件，提取代理节点"""
    global merged_proxies
    try:
        content = yaml.safe_load(data)
        proxies = content.get('proxies', [])
        if proxies:
            merged_proxies.extend(proxies)
        else:
            logging.warning(f"No proxy configuration found for index {index}")
    except Exception as e:
        logging.error(f"Error processing clash data for index {index}: {e}")

def tcp_connection_test(server: str, port: int, proto: str = "tcp", timeout: float = 5.0):
    """
    测试节点 TCP 可用性和延迟，跳过不支持 TCP 的协议
    返回 (是否成功, 平均延迟ms)
    
    server: 节点地址
    port: 节点端口
    proto: 节点协议，用于判断是否跳过
    """
    skip_protocols = {"hysteria", "hysteria2", "hy2", "tuic", "wireguard", "juicity"}
    if proto.lower() in skip_protocols:
        logging.info(f"跳过不支持 TCP 的协议: {proto}")
        return False, None

    try:
        latencies = []
        for _ in range(3):  # 测试 3 次取平均值
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                start_time = time.time()
                result = sock.connect_ex((server, port))
                end_time = time.time()
                sock.close()

                if result == 0:  # 0 表示连接成功
                    latency = (end_time - start_time) * 1000
                    latencies.append(latency)
            except Exception as e:
                logging.error(f"Connection error: {e}")

        if latencies:
            average_latency = sum(latencies) / len(latencies)
            return True, average_latency
        else:
            return False, None

    except Exception as e:
        logging.error(f"TCP connection test failed: {e}")
        return False, None

def check_proxies_availability(proxies, timeout=5.0):
    """检测代理的可用性和延迟"""
    available_proxies = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(
                tcp_connection_test,
                proxy.get("server"),
                int(proxy.get("port")),
                proxy.get("type", "tcp"),
                timeout
            ): proxy
            for proxy in proxies
            if proxy.get("server") and proxy.get("port")
        }

        for future in as_completed(futures):
            proxy = futures[future]
            server = proxy.get("server")
            port = proxy.get("port")
            name = proxy.get("name", "Unnamed")
            try:
                is_available, latency = future.result()
                if is_available and LATENCY_THRESHOLD >= latency >= MIN_LATENCY_THRESHOLD:
                    logging.info(f"Node ({name}): {server}:{port} is available, latency {latency:.2f} ms")
                    available_proxies.append(proxy)
                else:
                    logging.info(f"Node ({name}): {server}:{port} unavailable or latency out of range")
            except Exception as e:
                logging.error(f"Error checking proxy {name}: {e}")

    return available_proxies

def load_yaml(file_path):
    """加载 YAML 文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            return yaml.safe_load(file)
    except Exception as e:
        logging.error(f"Error loading YAML file {file_path}: {e}")
        return {}

def save_yaml(file_path, data):
    """保存数据到 YAML 文件"""
    try:
        with open(file_path, 'w', encoding='utf-8') as file:
            yaml.dump({'proxies': data}, file, sort_keys=False, allow_unicode=True)
    except Exception as e:
        logging.error(f"Error saving YAML file {file_path}: {e}")

def main():
    global merged_proxies
    merged_proxies = []

    # 从 URL 文件（或多个文件）中提取代理节点
    process_urls(URL_FILE_OR_FILES, process_clash)

    # 将合并后的代理列表写入 YAML 文件
    save_yaml(MERGED_PROXIES_FILE, merged_proxies)
    logging.info("Aggregation completed")

    # 加载生成的 YAML 文件并检测代理可用性
    proxies = load_yaml(MERGED_PROXIES_FILE).get("proxies", [])
    if proxies:
        available_proxies = check_proxies_availability(proxies)
        save_yaml(MERGED_PROXIES_FILE, available_proxies)
        logging.info(f"Available proxies updated and saved to {MERGED_PROXIES_FILE}")
    else:
        logging.warning("No proxies found to check")

if __name__ == "__main__":
    main()
