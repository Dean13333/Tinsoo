import base64
from flask import Flask, request, jsonify, g, send_file, send_from_directory
from flask_cors import CORS
from flask_swagger_ui import get_swaggerui_blueprint
import mysql.connector
import bcrypt
import uuid
import os
from functools import wraps
import io
from dotenv import load_dotenv
import jwt
from datetime import datetime, timedelta

app = Flask(__name__)


CORS(app)

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "wFe15lQsD8E5AFytMydhzrNAylp3FIzcTBLGPS-Haoo")

SWAGGER_URL = '/swagger'
API_URL = '/static/swagger.yaml'  # 你的 Swagger 規範文件位置
swaggerui_blueprint = get_swaggerui_blueprint(SWAGGER_URL, API_URL, config={
    'app_name': "Tinsoo API"
})
app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)

# 提供 Swagger 規範文件
@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)

def get_db():
    if 'db' not in g:
        g.db = mysql.connector.connect(
            host=os.getenv('DB_HOST'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            database=os.getenv('DB_NAME'),
            auth_plugin='mysql_native_password'
        )
        g.cursor = g.db.cursor()
    return g.db, g.cursor

@app.teardown_appcontext
def close_connection(exception):
    db = g.pop('db', None)
    if db is not None and db.is_connected():
        db.close()

@app.route('/register', methods=['POST'])
def register_user():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    nickname = data.get('nickname', '未命名用戶')  # 預設暱稱
    avatar = data.get('avatar', None)  # 預設無頭貼
    user_id = str(uuid.uuid4())
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    
    try:
        db, cursor = get_db()
        cursor.execute("""
            INSERT INTO users (email, password_hash, user_id, avatar, nickname) 
            VALUES (%s, %s, %s, %s, %s)
        """, (email, hashed_password.decode('utf-8'), user_id, avatar, nickname))
        db.commit()
        return jsonify({"message": "註冊成功！您的 UserID 是：", "user_id": user_id}), 201
    except mysql.connector.Error as err:
        return jsonify({"error": str(err)}), 500


@app.route('/login', methods=['POST'])
def login_user():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    
    try:
        db, cursor = get_db()
        cursor.execute("SELECT password_hash FROM users WHERE email = %s", (email,))
        result = cursor.fetchone()
        if result:
            stored_password_hash = result[0]
            if bcrypt.checkpw(password.encode('utf-8'), stored_password_hash.encode('utf-8')):
                # 生成 JWT
                token = jwt.encode({
                    "email": email,
                    "exp": datetime.utcnow() + timedelta(hours=1)  # Token 有效期 1 小時
                }, SECRET_KEY, algorithm="HS256")
                return jsonify({"message": "登入成功！", "token": token}), 200
            else:
                return jsonify({"error": "密碼錯誤！"}), 401
        else:
            return jsonify({"error": "找不到此帳號！"}), 404
    except mysql.connector.Error as err:
        return jsonify({"error": str(err)}), 500

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization')  # 從請求頭中取得 Token
        if not token:
            return jsonify({"error": "請先登入！"}), 401

        try:
            # 驗證 JWT
            decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            g.current_user = decoded["email"]  # 將解碼的用戶 Email 存入全局變數 g
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "登入已過期，請重新登入！"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "無效的 Token，請重新登入！"}), 401

        return f(*args, **kwargs)
    return decorated_function

@app.route('/profile', methods=['GET'])
@login_required
def get_profile():
    email = g.current_user  # 從全局變數 g 中獲取當前用戶的 Email
    
    try:
        db, cursor = get_db()
        cursor.execute("""
            SELECT email, nickname, avatar 
            FROM users 
            WHERE email = %s
        """, (email,))
        user = cursor.fetchone()

        if not user:
            return jsonify({"error": "找不到此用戶！"}), 404

        email, nickname, avatar = user
        return jsonify({
            "email": email,
            "nickname": nickname,
            "avatar": avatar
        }), 200
    except mysql.connector.Error as err:
        return jsonify({"error": str(err)}), 500


# 定義 import_book decorator
def import_book(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        data = request.json
        book_name = data.get('book_name')
        writer = data.get('writer')
        publishing = data.get('publishing')
        description = data.get('description')

        if not book_name or not writer or not description:
            return jsonify({"error": "書名、作者和簡介不能為空！"}), 400
        
        try:
            db, cursor = get_db()
            cursor.execute(
                "INSERT INTO books (book_name, writer, publishing, description) VALUES (%s, %s, %s, %s)",
                (book_name, writer, publishing, description)
            )
            db.commit()
            book_id = cursor.lastrowid  # 獲取自動生成的 book_id
            return f(book_id, *args, **kwargs)
        except mysql.connector.Error as err:
            return jsonify({"error": str(err)}), 500
    
    return decorated_function

# 更新存放書籍封面圖片的 decorator，改為從路徑讀取圖片
def upload_book_cover(f):
    @wraps(f)
    def decorated_function(book_id, *args, **kwargs):
        data = request.json
        cover_image_path = data.get('cover_image_path')

        if not cover_image_path:
            return jsonify({"error": "封面圖片路徑是必需的"}), 400
        
        try:
            # 檢查文件是否存在
            if not os.path.exists(cover_image_path):
                return jsonify({"error": f"找不到封面圖片路徑: {cover_image_path}"}), 404

            # 使用路徑讀取封面圖片
            with open(cover_image_path, 'rb') as cover_image_file:
                cover_image_content = cover_image_file.read()

            db, cursor = get_db()

            # 存儲圖片的二進位內容到資料庫中的 cover_image 欄位
            cursor.execute(
                "UPDATE books SET cover_image = %s WHERE book_id = %s",
                (cover_image_content, book_id)  # 存圖片的二進位數據到資料庫
            )
            db.commit()

            return f(book_id, *args, **kwargs)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return decorated_function

# 匯入 PDF 並更新書籍封面
@app.route('/import_pdf', methods=['POST'])
@import_book
@upload_book_cover
def import_pdf_to_books_file(book_id):
    data = request.json
    pdf_path = data.get('pdf_path')

    if not pdf_path:
        return jsonify({"error": "PDF 文件路徑是必需的"}), 400

    try:
        # 檢查文件是否存在
        if not os.path.exists(pdf_path):
            return jsonify({"error": f"找不到 PDF 文件路徑: {pdf_path}"}), 404

        # 從指定路徑讀取 PDF 文件
        with open(pdf_path, 'rb') as pdf_file:
            file_content = pdf_file.read()

        file_name = os.path.basename(pdf_path)

        db, cursor = get_db()
        cursor.execute(
            "INSERT INTO books_file (file_name, file_content, file_id) VALUES (%s, %s, %s)",
            (file_name, file_content, book_id)
        )
        db.commit()

        return jsonify({"message": f"PDF 文件 '{file_name}' 和封面圖片已成功匯入。"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 根據書名搜尋, methods=['GET'])
@app.route('/search_book', methods=['GET'])
def search_book_by_name():
    book_name = request.args.get('book_name')
    
    if not book_name:
        return jsonify({"error": "書名不能為空"}), 400

    try:
        db, cursor = get_db()
        cursor.execute("SELECT * FROM books WHERE book_name LIKE %s", (f"%{book_name}%",))
        books = cursor.fetchall()
        
        if books:
            # 確保 description 欄位是字符串類型
            result = []
            for book in books:
                description = book[4]
                
                # 檢查是否為 bytes 類型
                if isinstance(description, bytes):
                    try:
                        description = description.decode('utf-8')  # 嘗試用 UTF-8 解碼
                    except UnicodeDecodeError:
                        description = description.decode('latin-1')  # 若 UTF-8 失敗，使用 Latin-1 解碼

                result.append({
                    "book_id": book[0],
                    "book_name": book[1],
                    "writer": book[2],
                    "publishing": book[3],
                    "description": description
                })

            return jsonify(result), 200
        else:
            return jsonify({"message": "未找到匹配的書籍"}), 404
    except mysql.connector.Error as err:
        return jsonify({"error": str(err)}), 500



# 提供特定書籍的 PDF 文件，顯示在瀏覽器中而不是下載
@app.route('/book/<int:book_id>/pdf', methods=['GET'])
def view_book_pdf(book_id):
    try:
        db, cursor = get_db()
        # 查詢 PDF 文件
        cursor.execute("SELECT file_name, file_content FROM books_file WHERE file_id = %s", (book_id,))
        pdf_file = cursor.fetchone()

        if not pdf_file:
            return jsonify({"error": "找不到 PDF 文件"}), 404

        pdf_file_name, pdf_file_content = pdf_file

        # 直接顯示 PDF 文件，而不是觸發下載
        return send_file(io.BytesIO(pdf_file_content),
                         mimetype='application/pdf',
                         download_name=pdf_file_name)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 提供特定書籍的封面圖片，顯示在瀏覽器中
@app.route('/book/<int:book_id>/cover', methods=['GET'])
def view_book_cover(book_id):
    try:
        db, cursor = get_db()
        # 查詢封面圖片
        cursor.execute("SELECT cover_image FROM books WHERE book_id = %s", (book_id,))
        cover_image = cursor.fetchone()

        if not cover_image:
            return jsonify({"error": "找不到封面圖片"}), 404

        cover_image_content = cover_image[0]

        # 直接顯示封面圖片
        return send_file(io.BytesIO(cover_image_content),
                         mimetype='image/jpeg')

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/users', methods=['GET'])
def get_all_users():
    try:
        db, cursor = get_db()
        cursor.execute("SELECT email, password_hash FROM users")
        users = cursor.fetchall()
        return jsonify([{"email": email, "password_hash": password_hash} for email, password_hash in users]), 200
    except mysql.connector.Error as err:
        return jsonify({"error": str(err)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/allbook', methods=['GET'])
def get_book():
    db, cursor = get_db()
    
    query = "SELECT book_id, book_name, cover_image FROM books"
    cursor.execute(query)
    books = cursor.fetchall()
    
   
    books_list = []
    for book in books:
        book_id, book_name, cover_image_content = book

        cover_image_data_uri = None
        if cover_image_content:
            cover_image_base64 = base64.b64encode(cover_image_content).decode('utf-8')
            cover_image_data_uri = f"data:image/jpeg;base64,{cover_image_base64}"

        books_list.append({
            "book_id": book_id,
            "book_name": book_name,
            "cover_image": cover_image_data_uri
        })

    
    html = "<html><body>"
    for book in books_list:
        html += f"<h2>{book['book_name']}</h2>"
        if book['cover_image']:
            html += f"<img src='{book['cover_image']}' width='200'><br>"
    html += "</body></html>"

    return html

@app.route('/showbooks', methods=['GET'])
def get_books():
    db, cursor = get_db()
    
    
    query = "SELECT book_id, book_name, cover_image, description FROM books"
    cursor.execute(query)
    books = cursor.fetchall()
    
    
    books_list = []
    for book in books:
        book_id, book_name, cover_image_content, description = book

        
        cover_image_data_uri = None
        if cover_image_content:
            cover_image_base64 = base64.b64encode(cover_image_content).decode('utf-8')
            cover_image_data_uri = f"data:image/jpeg;base64,{cover_image_base64}"

        books_list.append({
            "book_id": book_id,
            "book_name": book_name,
            "cover_image": cover_image_data_uri,
	    "description":description
        })

    
    return jsonify(books_list), 200

@app.route('/upload_audio', methods=['POST'])
def upload_audio_files():
    """
    上傳音訊檔案，支援單檔或多檔。
    請求格式：
    {
        "book_id": 2,
        "audio_files": [
            "/path/to/audio1.mp3",
            "/path/to/audio2.mp3"
        ]
    }
    """
    data = request.json
    book_id = data.get('book_id')
    audio_files = data.get('audio_files')  # 音訊檔案的路徑列表

    # 驗證輸入
    if not book_id:
        return jsonify({"error": "書籍 ID 是必需的"}), 400
    if not audio_files or not isinstance(audio_files, list):
        return jsonify({"error": "音訊檔案列表是必需的，且必須是列表格式"}), 400

    db, cursor = None, None
    try:
        db, cursor = get_db()

        # 遍歷音訊檔案列表並插入到資料庫
        for audio_file_path in audio_files:
            # 獲取檔案名稱
            audio_name = os.path.basename(audio_file_path)

            # 檢查音訊檔案是否存在
            if not os.path.exists(audio_file_path):
                return jsonify({"error": f"音訊檔案路徑不存在: {audio_file_path}"}), 404

            # 讀取音訊檔案內容
            with open(audio_file_path, 'rb') as audio_file:
                audio_data = audio_file.read()

            # 插入音訊檔案到 `book_audio` 表
            cursor.execute(
                "INSERT INTO book_audio (book_id, audio_name, audio_data) VALUES (%s, %s, %s)",
                (book_id, audio_name, audio_data)
            )

        db.commit()
        return jsonify({"message": f"{len(audio_files)} 個音訊檔案已成功新增到書籍 ID {book_id}"}), 201
    except mysql.connector.Error as err:
        if db:
            db.rollback()
        return jsonify({"error": str(err)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if db:
            db.close()

@app.route('/Getaudio/<int:audio_id>', methods=['GET'])
def get_audio_by_id(audio_id):
    """
    根據 audio_id 提供單個音檔
    """
    try:
        db, cursor = get_db()
        # 查詢音檔
        cursor.execute("SELECT audio_name, audio_data FROM book_audio WHERE audio_id = %s", (audio_id,))
        audio = cursor.fetchone()

        if not audio:
            return jsonify({"error": "找不到對應的音檔"}), 404

        audio_name, audio_data = audio

        # 返回音檔作為二進位流
        return send_file(
            io.BytesIO(audio_data),
            mimetype='audio/mpeg',  # 假設音檔是 MP3 格式
            download_name=audio_name
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/book/<int:book_id>/audios', methods=['GET'])
def get_audios_by_book_id(book_id):
    """
    根據 book_id 提供該書籍的所有音檔元數據
    """
    try:
        db, cursor = get_db()
        # 查詢與書籍相關的所有音檔
        cursor.execute("SELECT audio_id, audio_name FROM book_audio WHERE book_id = %s", (book_id,))
        audios = cursor.fetchall()

        if not audios:
            return jsonify({"message": "該書籍沒有相關的音檔"}), 404

        # 整理為 JSON 格式返回
        result = [
            {"audio_id": audio_id, "audio_name": audio_name}
            for audio_id, audio_name in audios
        ]
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0')

