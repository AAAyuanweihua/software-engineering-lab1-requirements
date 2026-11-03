"""Product ownership, moderation and versioned publication workflows."""

from pathlib import PurePath

from campus_market.core import Core
from campus_market.models import new_id, now, price_cents, required
from campus_market.validation import validated


class Catalog(Core):
    """Product changes are authorized against the current database row."""

    def product(self, connection, product_id):
        """Load a product in the caller's transaction."""
        return self.one(connection, "SELECT * FROM products WHERE id=?", (product_id,))

    def owned(self, connection, actor, product_id):
        """Admin status does not grant ordinary sellers edit ownership."""
        product = self.product(connection, product_id)
        self.require(product["seller_id"] == actor.id, "FORBIDDEN", "只能修改本人商品", 403)
        return product

    @staticmethod
    def set_product_state(connection, product_id, state):
        """Every state-changing write invalidates previously displayed product versions."""
        connection.execute(
            "UPDATE products SET status=?,version=version+1,updated_at=? WHERE id=?",
            (state, now(), product_id),
        )

    @validated
    def create_product(self, token, title, category, price, description, image_ref):
        """Save a complete draft with image-reference metadata (no image hosting)."""
        return self._save_product(token, None, title, category, price, description, image_ref)

    @validated
    def edit_product(self, token, product_id, title, category, price, description, image_ref):
        """Editing published details removes the item from public search until reapproval."""
        return self._save_product(token, product_id, title, category, price, description, image_ref)

    def _save_product(self, token, product_id, title, category, price, description, image_ref):
        fields = {
            "title": required(title, "标题", 120),
            "category": required(category, "分类", 40),
            "description": required(description, "描述"),
            "image_ref": required(image_ref, "图片文件名", 120),
            "price_cents": price_cents(price),
        }
        category = fields["category"]
        image = fields["image_ref"]
        self.require(
            PurePath(image).name == image
            and "/" not in image
            and "\\" not in image
            and PurePath(image).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"),
            "INVALID_IMAGE",
            "仅保存图片文件名引用，不接受路径；支持PNG/JPG/WEBP",
            422,
        )

        def operation(connection, actor):
            self.one(connection, "SELECT name FROM categories WHERE name=?", (category,))
            stamp = now()
            if product_id:
                product = self.owned(connection, actor, product_id)
                self.require(
                    product["status"] in ("DRAFT", "PUBLISHED") and not product["restricted"],
                    "STATE_CONFLICT",
                    "当前状态不能修改，请先处理预约或恢复草稿",
                )
                state = "REVIEWING" if product["status"] == "PUBLISHED" else "DRAFT"
                connection.execute(
                    "UPDATE products SET title=?,category=?,description=?,image_ref=?,"
                    "price_cents=?,status=?,version=version+1,updated_at=? WHERE id=?",
                    (
                        fields["title"],
                        category,
                        fields["description"],
                        image,
                        fields["price_cents"],
                        state,
                        stamp,
                        product_id,
                    ),
                )
                result_id = product_id
            else:
                result_id = new_id()
                connection.execute(
                    "INSERT INTO products(id,seller_id,title,category,description,image_ref,"
                    "price_cents,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'DRAFT',?,?)",
                    (
                        result_id,
                        actor.id,
                        fields["title"],
                        category,
                        fields["description"],
                        image,
                        fields["price_cents"],
                        stamp,
                        stamp,
                    ),
                )
            return self.product(connection, result_id)

        return self.write(
            token,
            "EDIT_PRODUCT" if product_id else "CREATE_PRODUCT",
            product_id or "",
            fields,
            operation,
        )

    @validated
    def submit_product(self, token, product_id):
        """Only unrestricted drafts enter the moderation queue."""

        def operation(connection, actor):
            product = self.owned(connection, actor, product_id)
            self.require(
                product["status"] == "DRAFT" and not product["restricted"],
                "STATE_CONFLICT",
                "仅未受限制的草稿可提交",
            )
            self.set_product_state(connection, product_id, "REVIEWING")
            return self.product(connection, product_id)

        return self.write(token, "SUBMIT", product_id, {}, operation)

    @validated
    def review_product(self, token, product_id, version, approve, reason=""):
        """Review the current submitted version; rejection requires a reason."""
        if not approve:
            reason = required(reason, "驳回原因", 500)

        def operation(connection, actor):
            product = self.product(connection, product_id)
            self.require(product["version"] == version, "STALE_VERSION", "审核版本已变化")
            self.require(
                product["status"] == "REVIEWING" and not product["restricted"],
                "STATE_CONFLICT",
                "仅审核当前未受限制的待审核商品",
            )
            state = "PUBLISHED" if approve else "DRAFT"
            self.set_product_state(connection, product_id, state)
            if approve:
                connection.execute(
                    "UPDATE products SET published_at=? WHERE id=?", (now(), product_id)
                )
            connection.execute(
                "INSERT INTO reviews VALUES (?,?,?,?,?,?,?)",
                (new_id(), product_id, actor.id, version, state, reason, now()),
            )
            return self.product(connection, product_id)

        return self.write(
            token,
            "REVIEW",
            product_id,
            {"version": version, "approve": approve, "reason": reason},
            operation,
            admin=True,
        )

    @validated
    def take_down(self, token, product_id):
        """Sellers cannot use ordinary takedown to bypass an active transaction."""

        def operation(connection, actor):
            product = self.owned(connection, actor, product_id)
            self.require(product["status"] == "PUBLISHED", "STATE_CONFLICT", "仅已发布可下架")
            self.set_product_state(connection, product_id, "OFFLINE")
            return self.product(connection, product_id)

        return self.write(token, "TAKE_DOWN", product_id, {}, operation)

    @validated
    def restore_draft(self, token, product_id):
        """Restoration never publishes directly and never revives completed items."""

        def operation(connection, actor):
            product = self.owned(connection, actor, product_id)
            self.require(
                product["status"] == "OFFLINE" and not product["restricted"],
                "STATE_CONFLICT",
                "须为已下架且已解除违规限制的商品",
            )
            self.set_product_state(connection, product_id, "DRAFT")
            return self.product(connection, product_id)

        return self.write(token, "RESTORE_DRAFT", product_id, {}, operation)
