"""Interactive desktop-terminal adapter with no public network listener."""

import argparse
import json
import shlex
from dataclasses import asdict
from getpass import getpass
from pathlib import Path

from campus_market.database import Database
from campus_market.demo import demonstrate
from campus_market.models import MarketError
from campus_market.queries import Market

OPERATIONS = (
    "create_product",
    "edit_product",
    "submit_product",
    "review_product",
    "take_down",
    "restore_draft",
    "list_products",
    "get_product",
    "reserve",
    "seller_decision",
    "cancel_pending",
    "request_cancellation",
    "decide_cancellation",
    "confirm",
    "get_reservation",
    "list_reservations",
    "restrict",
    "unrestrict",
    "disable_user",
    "close_disabled_trade",
    "seller_stats",
    "category_stats",
    "logs",
    "users",
)

HELP = """登录后使用：操作名 {JSON参数}，ID和版本从上一条结果复制。
登录/注册密码通过隐藏输入框读取，不写入命令参数。
  register student     注册普通账号（不能自行选择管理员）
  login student        登录并把会话保存在当前内存中
  me / logout / help / quit
  create-admin account 管理员受控创建新管理员，密码隐藏输入
示例：
  list_products {"keyword":"教材","minimum":"20","maximum":"200"}
  create_product {"title":"线代教材","category":"教材","price":"35.00",
                  "description":"无缺页","image_ref":"book.jpg"}
  submit_product {"product_id":"ID"}
  list_products {"scope":"admin","status":"REVIEWING"}
  review_product {"product_id":"ID","version":2,"approve":true}
  reserve {"product_id":"ID","version":3,"key":"本次操作唯一键"}
  seller_decision {"reservation_id":"ID","accept":true,"key":"accept-1"}
  confirm {"reservation_id":"ID","epoch":1,"key":"confirm-1"}
  request_cancellation {"reservation_id":"ID","reason":"不再需要","key":"cancel-1"}
  decide_cancellation {"cancellation_id":"ID","decision":"REJECTED","key":"decide-1"}
  seller_stats {"start":"2026-11-01","end":"2026-12-01"}
重试必须使用原请求键和原参数；一次新操作必须使用新键。
金额以字符串元输入，输出price_cents为整数分。图片仅保存引用，不提供上传/托管。
"""


def output(value):
    """One JSON envelope per result, suitable for screenshots or a future GUI."""
    print(json.dumps(value, ensure_ascii=False, indent=2))


class Shell:
    """Presentation state holds only the current bearer; services own all business rules."""

    def __init__(self, market):
        self.market = market
        self.token = ""

    def execute(self, line):
        """Dispatch only an explicit operation allowlist, never arbitrary Python names."""
        command, _, raw = line.strip().partition(" ")
        if command in ("help", "?"):
            print(HELP)
            print("全部业务操作：", ", ".join(OPERATIONS))
        elif command == "me":
            output(asdict(self.market.me(self.token)))
        elif command == "logout":
            self.market.logout(self.token)
            self.token = ""
            print("已退出登录")
        elif command in ("login", "register", "create-admin"):
            args = shlex.split(raw)
            if len(args) != 1:
                raise MarketError("INVALID_INPUT", "请提供一个账号名称", 422)
            password = getpass("密码（10至200字符，不回显）: ")
            if command == "login":
                # A failed account switch must not leave the previous identity active.
                if self.token:
                    self.market.logout(self.token)
                self.token = ""
                self.token = self.market.login(args[0], password)
                output(asdict(self.market.me(self.token)))
            elif command == "register":
                output({"id": self.market.register(args[0], password)})
            else:
                output(self.market.create_admin(self.token, args[0], password))
        elif command in OPERATIONS:
            params = json.loads(raw or "{}")
            if not isinstance(params, dict) or "token" in params:
                raise MarketError("INVALID_INPUT", "参数须为对象，身份使用当前登录会话", 422)
            result = getattr(self.market, command)(token=self.token, **params)
            output(result)
        else:
            raise MarketError("UNKNOWN_COMMAND", "未知命令，输入help查看用法", 422)

    def run(self):
        """Remain usable after business errors, malformed JSON or wrong arguments."""
        print("校园二手交易系统：help查看命令；quit退出。仅线下交易，不处理支付。")
        while True:
            try:
                line = input("market> ").strip()
                if line in ("quit", "exit"):
                    break
                if line:
                    self.execute(line)
            except MarketError as exc:
                output(exc.as_dict())
            except (json.JSONDecodeError, TypeError, ValueError):
                output({"error": "INVALID_INPUT", "message": "参数格式或类型有误，请查看help"})
            except (EOFError, KeyboardInterrupt):
                print("\n退出；业务数据已保存，未提交的操作不会生效。")
                break
        if self.token:
            self.market.logout(self.token)


def main():
    """Entry point installed by the wheel's campus-market console script."""
    parser = argparse.ArgumentParser(description="校园二手交易：本地事务原型")
    parser.add_argument("--db", type=Path, default=Path("market.db"), help="SQLite数据库路径")
    parser.add_argument("command", choices=("init-admin", "register", "shell", "demo"))
    parser.add_argument("--account", default="", help="初始化管理员或注册时使用的账号")
    args = parser.parse_args()
    try:
        if args.command == "demo":
            output(demonstrate(args.db))
            return 0
        market = Market(Database(args.db))
        if args.command in ("init-admin", "register"):
            if not args.account:
                parser.error("该命令需要--account")
            password = getpass("密码（不回显）: ")
            output({"id": market.register(args.account, password, args.command == "init-admin")})
        else:
            Shell(market).run()
        return 0
    except MarketError as exc:
        output(exc.as_dict())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
