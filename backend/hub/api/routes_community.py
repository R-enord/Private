"""Community publishing (Phase 5 slice): articles, comments, likes, author pages, moderation.

Private-first: articles default to ``visibility=private``. With registration disabled only the owner (and any
users the owner creates) can see content; 'public' only matters once a public deployment is approved.
Authorization is ownership-based from day one (authors edit their own; moderators can hide/remove).
Markdown is rendered and sanitized server-side; the UI never renders user HTML directly.
"""

from __future__ import annotations

import datetime as dt
import re
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from hub.api.deps import get_db, require
from hub.models import Article, Comment, Item, ModerationReport, Reaction, User
from hub.pipeline.canonical import safe_link
from hub.security.auth import can, can_modify_owned
from hub.security.sanitize import render_markdown
from hub.settings_store import audit

router = APIRouter(prefix="/api")
ARTICLE_TYPES = ("tutorial", "announcement", "review", "experiment", "comparison", "workflow", "project")


def _slugify(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80] or "post"
    return f"{base}-{secrets.token_hex(3)}"


def _visible(user: User) -> Any:
    """Articles a user may read: own (any status) + published ones that are not hidden/removed."""
    if can(user, "moderation:act"):
        return True
    return or_(Article.author_id == user.id, Article.status == "published")


def _article_out(db: Session, a: Article, user: User, full: bool = False) -> dict[str, Any]:
    author = db.get(User, a.author_id)
    likes = db.scalar(select(func.count()).select_from(Reaction).where(Reaction.target_type == "article",
                                                                        Reaction.target_id == a.id)) or 0
    liked = bool(db.scalar(select(func.count()).select_from(Reaction).where(
        Reaction.target_type == "article", Reaction.target_id == a.id, Reaction.user_id == user.id)))
    out: dict[str, Any] = {
        "id": a.id, "slug": a.slug, "title": a.title, "article_type": a.article_type, "tags": a.tags,
        "status": a.status, "visibility": a.visibility, "cover_image_url": safe_link(a.cover_image_url),
        "related_item_id": a.related_item_id, "created_at": a.created_at.isoformat(), "updated_at": a.updated_at.isoformat(),
        "published_at": a.published_at.isoformat() if a.published_at else None, "likes": likes, "liked": liked,
        "author": {"id": author.id, "handle": author.handle, "display_name": author.display_name} if author else None,
        "can_edit": can_modify_owned(user, a.author_id, "articles:own"),
    }
    if full:
        out["body_md"] = a.body_md
        out["body_html"] = a.body_html
    return out


class ArticleIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    body_md: str = Field(default="", max_length=200_000)
    article_type: str = Field(default="tutorial")
    tags: list[str] = Field(default_factory=list, max_length=12)
    cover_image_url: str = Field(default="", max_length=2000)
    related_item_id: int | None = None
    status: str = Field(default="draft", pattern="^(draft|published)$")
    visibility: str = Field(default="private", pattern="^(private|public)$")


def _apply(a: Article, body: ArticleIn, db: Session) -> None:
    if body.article_type not in ARTICLE_TYPES:
        raise HTTPException(400, f"article_type must be one of {', '.join(ARTICLE_TYPES)}")
    if body.cover_image_url and not safe_link(body.cover_image_url):
        raise HTTPException(400, "cover image must be an http(s) URL")
    if body.related_item_id is not None and db.get(Item, body.related_item_id) is None:
        raise HTTPException(400, "related item not found")
    a.title = body.title.strip()
    a.body_md = body.body_md
    a.body_html = render_markdown(body.body_md)
    a.article_type = body.article_type
    a.tags = [re.sub(r"[^a-z0-9-]", "", t.lower())[:40] for t in body.tags if t.strip()][:12]
    a.cover_image_url = body.cover_image_url
    a.related_item_id = body.related_item_id
    if body.status == "published" and a.status != "published":
        a.published_at = dt.datetime.now(dt.UTC)
    if a.status not in ("hidden", "removed"):  # moderation state is not overridable by authors
        a.status = body.status
    a.visibility = body.visibility
    a.updated_at = dt.datetime.now(dt.UTC)


@router.get("/articles")
def list_articles(author: str | None = None, tag: str | None = None, user: User = Depends(require("items:read")),
                  db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    q = select(Article).where(_visible(user)).order_by(Article.updated_at.desc()).limit(100)
    if author:
        q = q.join(User, User.id == Article.author_id).where(User.handle == author.lower())
    if tag:
        q = q.where(Article.tags.contains([tag.lower()]))
    return [_article_out(db, a, user) for a in db.scalars(q)]


@router.post("/articles")
def create_article(body: ArticleIn, user: User = Depends(require("articles:own")), db: Session = Depends(get_db)) -> dict[str, Any]:
    a = Article(author_id=user.id, slug=_slugify(body.title), title=body.title, status="draft")
    _apply(a, body, db)
    db.add(a)
    db.flush()
    audit(db, "article.create", user_id=user.id, target_type="article", target_id=a.id)
    db.commit()
    return _article_out(db, a, user, full=True)


def _get_visible(db: Session, slug_or_id: str, user: User) -> Article:
    a = db.scalar(select(Article).where(Article.slug == slug_or_id)) if not slug_or_id.isdigit() else db.get(Article, int(slug_or_id))
    if a is None:
        raise HTTPException(404, "not found")
    if not (can(user, "moderation:act") or a.author_id == user.id or a.status == "published"):
        raise HTTPException(404, "not found")
    return a


@router.get("/articles/{slug}")
def get_article(slug: str, user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    a = _get_visible(db, slug, user)
    out = _article_out(db, a, user, full=True)
    out["comments"] = _comments(db, user, article_id=a.id)
    return out


@router.put("/articles/{article_id}")
def update_article(article_id: int, body: ArticleIn, user: User = Depends(require("articles:own")),
                   db: Session = Depends(get_db)) -> dict[str, Any]:
    a = db.get(Article, article_id)
    if a is None or not can_modify_owned(user, a.author_id, "articles:own", elevated="users:admin"):
        raise HTTPException(404, "not found")
    _apply(a, body, db)
    audit(db, "article.update", user_id=user.id, target_type="article", target_id=a.id)
    db.commit()
    return _article_out(db, a, user, full=True)


@router.delete("/articles/{article_id}")
def delete_article(article_id: int, user: User = Depends(require("articles:own")), db: Session = Depends(get_db)) -> dict[str, Any]:
    a = db.get(Article, article_id)
    if a is None or not can_modify_owned(user, a.author_id, "articles:own", elevated="users:admin"):
        raise HTTPException(404, "not found")
    db.delete(a)
    audit(db, "article.delete", user_id=user.id, target_type="article", target_id=article_id)
    db.commit()
    return {"deleted": True}


@router.post("/articles/preview")
def preview_markdown(body: dict[str, Any], user: User = Depends(require("articles:own"))) -> dict[str, Any]:
    md = str(body.get("body_md", ""))[:200_000]
    return {"html": render_markdown(md)}


# ----------------------------------------------------------------------------- reactions


@router.post("/reactions/{target_type}/{target_id}")
def toggle_like(target_type: str, target_id: int, user: User = Depends(require("comments:own")),
                db: Session = Depends(get_db)) -> dict[str, Any]:
    if target_type not in ("article", "comment", "item"):
        raise HTTPException(400, "invalid target")
    exists = db.scalar(select(func.count()).select_from(Reaction).where(
        Reaction.user_id == user.id, Reaction.target_type == target_type, Reaction.target_id == target_id))
    if exists:
        db.execute(text("DELETE FROM reactions WHERE user_id=:u AND target_type=:t AND target_id=:i"),
                   {"u": user.id, "t": target_type, "i": target_id})
    else:
        db.add(Reaction(user_id=user.id, target_type=target_type, target_id=target_id, reaction="like"))
    db.commit()
    return {"liked": not exists}


# ----------------------------------------------------------------------------- comments


def _comments(db: Session, user: User, *, article_id: int | None = None, item_id: int | None = None) -> list[dict[str, Any]]:
    q = select(Comment, User).join(User, User.id == Comment.author_id).order_by(Comment.created_at)
    q = q.where(Comment.article_id == article_id) if article_id else q.where(Comment.item_id == item_id)
    if not can(user, "moderation:act"):
        q = q.where(or_(Comment.status == "visible", Comment.author_id == user.id))
    return [{"id": c.id, "parent_id": c.parent_id, "body_html": c.body_html, "status": c.status,
             "created_at": c.created_at.isoformat(), "author": {"handle": u.handle, "display_name": u.display_name},
             "can_edit": can_modify_owned(user, c.author_id, "comments:own")} for c, u in db.execute(q)]


class CommentIn(BaseModel):
    body_md: str = Field(min_length=1, max_length=20_000)
    article_id: int | None = None
    item_id: int | None = None
    parent_id: int | None = None


@router.get("/comments")
def list_comments(article_id: int | None = None, item_id: int | None = None, user: User = Depends(require("items:read")),
                  db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    if not (article_id or item_id):
        raise HTTPException(400, "article_id or item_id required")
    if article_id:
        _get_visible(db, str(article_id), user)
    return _comments(db, user, article_id=article_id, item_id=item_id)


@router.post("/comments")
def add_comment(body: CommentIn, user: User = Depends(require("comments:own")), db: Session = Depends(get_db)) -> dict[str, Any]:
    if bool(body.article_id) == bool(body.item_id):
        raise HTTPException(400, "exactly one of article_id or item_id")
    if body.article_id:
        _get_visible(db, str(body.article_id), user)
    elif db.get(Item, body.item_id) is None:
        raise HTTPException(404, "item not found")
    c = Comment(author_id=user.id, article_id=body.article_id, item_id=body.item_id, parent_id=body.parent_id,
                body_md=body.body_md, body_html=render_markdown(body.body_md))
    db.add(c)
    db.commit()
    return {"id": c.id, "body_html": c.body_html}


# ----------------------------------------------------------------------------- authors & moderation


@router.get("/authors/{handle}")
def author_page(handle: str, user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    u = db.scalar(select(User).where(User.handle == handle.lower(), User.is_active.is_(True)))
    if u is None:
        raise HTTPException(404, "not found")
    arts = db.scalars(select(Article).where(Article.author_id == u.id, _visible(user)).order_by(Article.updated_at.desc())).all()
    return {"handle": u.handle, "display_name": u.display_name, "bio": u.bio, "joined_at": u.created_at.isoformat(),
            "articles": [_article_out(db, a, user) for a in arts]}


class ReportIn(BaseModel):
    target_type: str = Field(pattern="^(article|comment)$")
    target_id: int
    reason: str = Field(min_length=3, max_length=2000)


@router.post("/moderation/reports")
def report_content(body: ReportIn, user: User = Depends(require("comments:own")), db: Session = Depends(get_db)) -> dict[str, Any]:
    r = ModerationReport(reporter_id=user.id, target_type=body.target_type, target_id=body.target_id, reason=body.reason)
    db.add(r)
    db.commit()
    return {"id": r.id, "status": r.status}


@router.get("/moderation/reports")
def list_reports(user: User = Depends(require("moderation:act")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [{"id": r.id, "target_type": r.target_type, "target_id": r.target_id, "reason": r.reason, "status": r.status,
             "created_at": r.created_at.isoformat()} for r in db.scalars(select(ModerationReport).order_by(ModerationReport.id.desc()).limit(200))]


class ModAction(BaseModel):
    action: str = Field(pattern="^(hide|remove|restore|dismiss)$")
    resolution: str = Field(default="", max_length=2000)


@router.post("/moderation/reports/{report_id}")
def act_on_report(report_id: int, body: ModAction, user: User = Depends(require("moderation:act")),
                  db: Session = Depends(get_db)) -> dict[str, Any]:
    r = db.get(ModerationReport, report_id)
    if r is None:
        raise HTTPException(404, "not found")
    target: Article | Comment | None = db.get(Article, r.target_id) if r.target_type == "article" else db.get(Comment, r.target_id)
    if body.action != "dismiss" and target is not None:
        target.status = {"hide": "hidden", "remove": "removed", "restore": "published" if r.target_type == "article" else "visible"}[body.action]
    r.status = "resolved" if body.action != "dismiss" else "dismissed"
    r.resolution, r.resolved_at, r.resolved_by = body.resolution or body.action, dt.datetime.now(dt.UTC), user.id
    audit(db, f"moderation.{body.action}", user_id=user.id, target_type=r.target_type, target_id=r.target_id)
    db.commit()
    return {"id": r.id, "status": r.status, "target_status": getattr(target, "status", None)}
