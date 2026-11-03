"""Executable, credential-free-output demonstration of the transaction lifecycle."""

import secrets

from campus_market.database import Database
from campus_market.models import MarketError, new_id
from campus_market.queries import Market


def demonstrate(path):
    """Use a new database and fresh passwords; never print or ship live credentials."""
    if path.exists():
        raise MarketError("DB_EXISTS", "演示须使用新数据库路径，避免覆盖已有数据", 422)
    market = Market(Database(path))
    accounts = {}
    suffix = new_id()[:8]
    for name in ("admin", "seller", "buyer"):
        password = secrets.token_urlsafe(20)
        account = name + suffix
        market.register(account, password, bootstrap=name == "admin")
        accounts[name] = market.login(account, password)
    admin, seller, buyer = (accounts[name] for name in ("admin", "seller", "buyer"))
    events = []
    draft = market.create_product(seller, "线性代数教材", "教材", "35.00", "九成新，无缺页", "book.jpg")
    events.append({"step": "draft", "status": draft["status"]})
    submitted = market.submit_product(seller, draft["id"])
    published = market.review_product(admin, draft["id"], submitted["version"], True)
    events.append({"step": "review", "status": published["status"]})
    key = new_id()
    reservation = market.reserve(buyer, draft["id"], published["version"], key)
    assert market.reserve(buyer, draft["id"], published["version"], key) == reservation
    events.append({"step": "reserve_retry", "same_result": True, "price_cents": reservation["price_cents"]})
    accepted = market.seller_decision(seller, reservation["id"], True, new_id())
    first = market.confirm(buyer, reservation["id"], accepted["epoch"], new_id())
    events.append({"step": "one_party", "status": first["status"]})
    cancellation = market.request_cancellation(buyer, reservation["id"], "暂时无法交付", new_id())
    reset = market.decide_cancellation(seller, cancellation["id"], "REJECTED", new_id())
    events.append({"step": "cancel_rejected", "epoch": reset["epoch"],
                   "flags": [reset["buyer_confirmed"], reset["seller_confirmed"]]})
    try:
        market.confirm(buyer, reservation["id"], first["epoch"], new_id())
    except MarketError as exc:
        events.append({"step": "old_confirmation", "error": exc.code})
    market.confirm(buyer, reservation["id"], reset["epoch"], new_id())
    completion_key = new_id()
    completed = market.confirm(seller, reservation["id"], reset["epoch"], completion_key)
    retry = market.confirm(seller, reservation["id"], reset["epoch"], completion_key)
    events.append({"step": "completed", "status": completed["status"],
                   "stable_time": completed["completed_at"] == retry["completed_at"]})
    restricted = market.restrict(admin, draft["id"], "演示完成后追加违规标记")
    events.append({"step": "post_completion_moderation",
                   "status": restricted["product"]["status"],
                   "restricted": restricted["product"]["restricted"]})
    events.append({"step": "seller_stats", **market.seller_stats(seller, "2020-01-01", "2100-01-01")})
    events.append({"step": "audit_count", "count": len(market.logs(admin))})
    return events
