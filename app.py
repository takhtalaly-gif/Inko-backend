"""
🚀 INKO Backend API Server
For Render.com deployment with Supabase
"""

import os
import json
import hashlib
import uuid
import time
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Database connection
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise Exception("DATABASE_URL environment variable not set!")

def get_db():
    """Get database connection"""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

def hash_password(password):
    """Hash password using SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()

def format_timestamp(dt):
    """Convert datetime to unix timestamp"""
    if isinstance(dt, datetime):
        return int(dt.timestamp())
    return dt

# ==================== AUTH ROUTES ====================

@app.route('/api/auth/signup', methods=['POST'])
def signup():
    """Register new user"""
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        return jsonify({'error': 'Missing required fields'}), 400
    
    if len(username) < 3 or len(username) > 30:
        return jsonify({'error': 'Username must be 3-30 characters'}), 400
    
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Check if username exists
        cur.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cur.fetchone():
            return jsonify({'error': 'Username already exists'}), 400
        
        # Create user
        hashed_pw = hash_password(password)
        cur.execute(
            "INSERT INTO users (username, password) VALUES (%s, %s) RETURNING id, username, bio, profile_pic, created_at",
            (username, hashed_pw)
        )
        user = dict(cur.fetchone())
        user['created_at'] = format_timestamp(user['created_at'])
        conn.commit()
        
        return jsonify({'success': True, 'user': user})
    
    except Exception as e:
        conn.rollback()
        print(f"Signup error: {e}")
        return jsonify({'error': 'Signup failed'}), 500
    
    finally:
        cur.close()
        conn.close()

@app.route('/api/auth/login', methods=['POST'])
def login():
    """Login user"""
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        return jsonify({'error': 'Missing required fields'}), 400
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute(
            "SELECT id, username, password, bio, profile_pic, created_at FROM users WHERE username = %s",
            (username,)
        )
        user = cur.fetchone()
        
        if not user or dict(user)['password'] != hash_password(password):
            return jsonify({'error': 'Invalid credentials'}), 401
        
        user_dict = dict(user)
        del user_dict['password']
        user_dict['created_at'] = format_timestamp(user_dict['created_at'])
        
        return jsonify({'success': True, 'user': user_dict})
    
    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({'error': 'Login failed'}), 500
    
    finally:
        cur.close()
        conn.close()

# ==================== FEED ROUTES ====================

@app.route('/api/feed', methods=['GET'])
def get_feed():
    """Get user feed"""
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Get posts from followed users + own posts
        cur.execute("""
            SELECT DISTINCT p.*, u.username, u.profile_pic as user_profile_pic,
                   (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as likes_count,
                   (SELECT COUNT(*) FROM comments WHERE post_id = p.id) as comments_count,
                   (SELECT json_agg(user_id) FROM likes WHERE post_id = p.id) as likes
            FROM posts p
            JOIN users u ON p.user_id = u.id
            WHERE p.user_id IN (
                SELECT following_id FROM follows WHERE follower_id = %s
                UNION SELECT %s
            )
            ORDER BY p.created_at DESC
            LIMIT 50
        """, (user_id, user_id))
        
        posts = []
        for row in cur.fetchall():
            post = dict(row)
            post['created_at'] = format_timestamp(post['created_at'])
            post['likes'] = post['likes'] or []
            posts.append(post)
        
        return jsonify({'posts': posts})
    
    except Exception as e:
        print(f"Feed error: {e}")
        return jsonify({'error': 'Failed to load feed'}), 500
    
    finally:
        cur.close()
        conn.close()

# ==================== EXPLORE ROUTES ====================

@app.route('/api/explore', methods=['GET'])
def get_explore():
    """Get explore posts"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT p.*, u.username, u.profile_pic as user_profile_pic,
                   (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as likes_count,
                   (SELECT COUNT(*) FROM comments WHERE post_id = p.id) as comments_count
            FROM posts p
            JOIN users u ON p.user_id = u.id
            ORDER BY p.created_at DESC
            LIMIT 30
        """)
        
        posts = []
        for row in cur.fetchall():
            post = dict(row)
            post['created_at'] = format_timestamp(post['created_at'])
            posts.append(post)
        
        return jsonify({'posts': posts})
    
    except Exception as e:
        print(f"Explore error: {e}")
        return jsonify({'error': 'Failed to load explore'}), 500
    
    finally:
        cur.close()
        conn.close()

# ==================== POST ROUTES ====================

@app.route('/api/post/like', methods=['POST'])
def like_post():
    """Like/unlike a post"""
    data = request.json
    user_id = data.get('user_id')
    post_id = data.get('post_id')
    
    if not user_id or not post_id:
        return jsonify({'error': 'Missing required fields'}), 400
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Check if already liked
        cur.execute("SELECT id FROM likes WHERE user_id = %s AND post_id = %s", (user_id, post_id))
        existing = cur.fetchone()
        
        if existing:
            # Unlike
            cur.execute("DELETE FROM likes WHERE user_id = %s AND post_id = %s", (user_id, post_id))
            liked = False
        else:
            # Like
            cur.execute("INSERT INTO likes (user_id, post_id) VALUES (%s, %s)", (user_id, post_id))
            
            # Create notification
            cur.execute("SELECT user_id FROM posts WHERE id = %s", (post_id,))
            post_owner = cur.fetchone()
            if post_owner and dict(post_owner)['user_id'] != user_id:
                cur.execute(
                    "INSERT INTO notifications (user_id, from_user_id, type, post_id) VALUES (%s, %s, 'like', %s)",
                    (dict(post_owner)['user_id'], user_id, post_id)
                )
            liked = True
        
        conn.commit()
        return jsonify({'success': True, 'liked': liked})
    
    except Exception as e:
        conn.rollback()
        print(f"Like error: {e}")
        return jsonify({'error': 'Failed to like post'}), 500
    
    finally:
        cur.close()
        conn.close()

@app.route('/api/post/comment', methods=['POST'])
def add_comment():
    """Add comment to post"""
    data = request.json
    user_id = data.get('user_id')
    post_id = data.get('post_id')
    text = data.get('text', '').strip()
    
    if not user_id or not post_id or not text:
        return jsonify({'error': 'Missing required fields'}), 400
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Add comment
        cur.execute(
            "INSERT INTO comments (user_id, post_id, text) VALUES (%s, %s, %s) RETURNING id, created_at",
            (user_id, post_id, text[:500])
        )
        comment = dict(cur.fetchone())
        comment['created_at'] = format_timestamp(comment['created_at'])
        
        # Create notification
        cur.execute("SELECT user_id FROM posts WHERE id = %s", (post_id,))
        post_owner = cur.fetchone()
        if post_owner and dict(post_owner)['user_id'] != user_id:
            cur.execute(
                "INSERT INTO notifications (user_id, from_user_id, type, post_id) VALUES (%s, %s, 'comment', %s)",
                (dict(post_owner)['user_id'], user_id, post_id)
            )
        
        conn.commit()
        return jsonify({'success': True, 'comment': comment})
    
    except Exception as e:
        conn.rollback()
        print(f"Comment error: {e}")
        return jsonify({'error': 'Failed to add comment'}), 500
    
    finally:
        cur.close()
        conn.close()

@app.route('/api/post/comments', methods=['GET'])
def get_comments():
    """Get post comments"""
    post_id = request.args.get('post_id')
    if not post_id:
        return jsonify({'error': 'Missing post_id'}), 400
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT c.*, u.username, u.profile_pic as user_profile_pic
            FROM comments c
            JOIN users u ON c.user_id = u.id
            WHERE c.post_id = %s
            ORDER BY c.created_at ASC
        """, (post_id,))
        
        comments = []
        for row in cur.fetchall():
            comment = dict(row)
            comment['created_at'] = format_timestamp(comment['created_at'])
            comments.append(comment)
        
        return jsonify({'comments': comments})
    
    except Exception as e:
        print(f"Get comments error: {e}")
        return jsonify({'error': 'Failed to get comments'}), 500
    
    finally:
        cur.close()
        conn.close()

# ==================== FOLLOW ROUTES ====================

@app.route('/api/follow', methods=['POST'])
def toggle_follow():
    """Follow/unfollow user"""
    data = request.json
    follower_id = data.get('follower_id')
    following_id = data.get('following_id')
    
    if not follower_id or not following_id:
        return jsonify({'error': 'Missing required fields'}), 400
    
    if follower_id == following_id:
        return jsonify({'error': 'Cannot follow yourself'}), 400
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Check if already following
        cur.execute("SELECT id FROM follows WHERE follower_id = %s AND following_id = %s", (follower_id, following_id))
        existing = cur.fetchone()
        
        if existing:
            # Unfollow
            cur.execute("DELETE FROM follows WHERE follower_id = %s AND following_id = %s", (follower_id, following_id))
            followed = False
        else:
            # Follow
            cur.execute("INSERT INTO follows (follower_id, following_id) VALUES (%s, %s)", (follower_id, following_id))
            
            # Create notification
            cur.execute(
                "INSERT INTO notifications (user_id, from_user_id, type) VALUES (%s, %s, 'follow')",
                (following_id, follower_id)
            )
            followed = True
        
        conn.commit()
        return jsonify({'success': True, 'followed': followed})
    
    except Exception as e:
        conn.rollback()
        print(f"Follow error: {e}")
        return jsonify({'error': 'Failed to follow'}), 500
    
    finally:
        cur.close()
        conn.close()

# ==================== SEARCH ROUTES ====================

@app.route('/api/users/search', methods=['GET'])
def search_users():
    """Search users"""
    query = request.args.get('query', '').strip()
    user_id = request.args.get('user_id')
    
    if not query:
        return jsonify({'users': []})
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT u.id, u.username, u.bio, u.profile_pic,
                   EXISTS(SELECT 1 FROM follows WHERE follower_id = %s AND following_id = u.id) as is_following
            FROM users u
            WHERE u.username ILIKE %s AND u.id != %s
            LIMIT 20
        """, (user_id, f'%{query}%', user_id))
        
        users = [dict(row) for row in cur.fetchall()]
        return jsonify({'users': users})
    
    except Exception as e:
        print(f"Search error: {e}")
        return jsonify({'error': 'Search failed'}), 500
    
    finally:
        cur.close()
        conn.close()

# ==================== PROFILE ROUTES ====================

@app.route('/api/profile', methods=['GET'])
def get_profile():
    """Get user profile"""
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Get user info
        cur.execute("SELECT id, username, bio, profile_pic, created_at FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        profile = dict(user)
        profile['created_at'] = format_timestamp(profile['created_at'])
        
        # Get posts
        cur.execute("""
            SELECT p.*, 
                   (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as likes_count,
                   (SELECT COUNT(*) FROM comments WHERE post_id = p.id) as comments_count
            FROM posts p
            WHERE p.user_id = %s
            ORDER BY p.created_at DESC
        """, (user_id,))
        
        posts = []
        for row in cur.fetchall():
            post = dict(row)
            post['created_at'] = format_timestamp(post['created_at'])
            posts.append(post)
        
        # Get counts
        cur.execute("SELECT COUNT(*) FROM follows WHERE following_id = %s", (user_id,))
        followers_count = cur.fetchone()['count']
        
        cur.execute("SELECT COUNT(*) FROM follows WHERE follower_id = %s", (user_id,))
        following_count = cur.fetchone()['count']
        
        return jsonify({
            'profile': profile,
            'posts': posts,
            'posts_count': len(posts),
            'followers_count': followers_count,
            'following_count': following_count
        })
    
    except Exception as e:
        print(f"Profile error: {e}")
        return jsonify({'error': 'Failed to load profile'}), 500
    
    finally:
        cur.close()
        conn.close()

# ==================== NOTIFICATIONS ROUTES ====================

@app.route('/api/notifications', methods=['GET'])
def get_notifications():
    """Get user notifications"""
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT n.*, u.username as from_username, u.profile_pic as from_profile_pic,
                   p.media_url as post_media
            FROM notifications n
            JOIN users u ON n.from_user_id = u.id
            LEFT JOIN posts p ON n.post_id = p.id
            WHERE n.user_id = %s
            ORDER BY n.created_at DESC
            LIMIT 50
        """, (user_id,))
        
        notifications = []
        for row in cur.fetchall():
            notif = dict(row)
            notif['created_at'] = format_timestamp(notif['created_at'])
            notifications.append(notif)
        
        # Get unread count
        cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id = %s AND read = FALSE", (user_id,))
        unread_count = cur.fetchone()['count']
        
        return jsonify({
            'notifications': notifications,
            'unread_count': unread_count
        })
    
    except Exception as e:
        print(f"Notifications error: {e}")
        return jsonify({'error': 'Failed to load notifications'}), 500
    
    finally:
        cur.close()
        conn.close()

@app.route('/api/notification/read', methods=['POST'])
def mark_notification_read():
    """Mark notification as read"""
    data = request.json
    user_id = data.get('user_id')
    notification_id = data.get('notification_id')
    
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        if notification_id:
            # Mark specific notification
            cur.execute("UPDATE notifications SET read = TRUE WHERE id = %s AND user_id = %s", (notification_id, user_id))
        else:
            # Mark all as read
            cur.execute("UPDATE notifications SET read = TRUE WHERE user_id = %s", (user_id,))
        
        conn.commit()
        return jsonify({'success': True})
    
    except Exception as e:
        conn.rollback()
        print(f"Mark read error: {e}")
        return jsonify({'error': 'Failed to mark as read'}), 500
    
    finally:
        cur.close()
        conn.close()

# ==================== HEALTH CHECK ====================

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'ok', 'message': 'INKO API is running'})

@app.route('/', methods=['GET'])
def home():
    """Home endpoint"""
    return jsonify({
        'name': 'INKO API',
        'version': '1.0',
        'status': 'running',
        'endpoints': [
            '/api/auth/signup',
            '/api/auth/login',
            '/api/feed',
            '/api/explore',
            '/api/post/like',
            '/api/post/comment',
            '/api/post/comments',
            '/api/follow',
            '/api/users/search',
            '/api/profile',
            '/api/notifications',
            '/api/notification/read'
        ]
    })

# ==================== RUN SERVER ====================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
