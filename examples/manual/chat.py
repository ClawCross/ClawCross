"""
WeBot Agent Python 测试客户端
用法：python tools/manual/chat.py
与运行中的 Agent 进行交互式对话测试。
"""
import requests


def send_message(user_id: str, text: str):
    """向 Agent 发送消息并获取响应。"""
    api_url = "http://127.0.0.1:51200/ask"
    payload = {
        "user_id": user_id,
        "text": text,
    }
    headers = {
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(api_url, json=payload, headers=headers)
        response.raise_for_status()
        result = response.json()
        print(f"\n[Agent 响应]: {result}")
    except Exception as e:
        print(f"\n[错误]: {e}")


if __name__ == "__main__":
    print("=== WeBot Agent Python 测试客户端 ===")
    print("(输入 'exit' 退出对话)")

    user_id = "WeBot_01"

    while True:
        user_input = input("\n[你]: ")
        if user_input.lower() == "exit":
            break
        if not user_input.strip():
            continue

        send_message(user_id, user_input)
