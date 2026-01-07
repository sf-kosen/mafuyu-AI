# Mafuyu CLI - 自律判断型
# 普通に会話するだけで真冬がツール/Codexを使う

from mafuyu import MafuyuSession


def main():
    print("=" * 50)
    print("  真冬ちゃん (自律判断型)")
    print("  普通に話しかけてね！")
    print("=" * 50)
    print()
    print("コマンド:")
    print("  /clear  - 会話履歴クリア")
    print("  /exit   - 終了")
    print()
    
    session = MafuyuSession()
    
    while True:
        try:
            user_input = input("you> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nまたね、オタク君♪")
            break
        
        if not user_input:
            continue
        
        # Commands
        if user_input.lower() == "/exit":
            print("またね、オタク君♪")
            break
        
        if user_input.lower() == "/clear":
            session.clear_history()
            print("[履歴クリアしたよ]")
            continue
        
        if user_input.startswith("/"):
            print("[知らないコマンドだよ？]")
            continue
        
        # Normal chat - Mafuyu decides what to do
        try:
            response = session.respond(user_input)
            print(f"mafuyu> {response}")
        except Exception as e:
            print(f"[エラー: {e}]")
        
        print()


if __name__ == "__main__":
    main()
