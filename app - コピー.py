import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
import chardet
import os
import json

# --- Flask Webアプリのメイン部分 ---
app = Flask(__name__)
# 秘密鍵をセッションに使うための設定
app.secret_key = os.urandom(24)
app.config['UPLOAD_FOLDER'] = 'uploads'

# uploadsフォルダが存在しない場合は作成
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# --- データベースの設定 ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///den_suke.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy()
db.init_app(app)

# --- モデルの定義 ---
class Attendee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(10), nullable=False)
    member = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(5), nullable=False)

    __table_args__ = (db.UniqueConstraint('date', 'member', name='unique_attendee'),)

    def __repr__(self):
        return f'<Attendee date={self.date} member={self.member} status={self.status}>'

# --- 共通のヘルパー関数 ---
def get_ok_symbols(level_type):
    """レベルタイプに応じて、参加可能とみなす記号のリストを返す"""
    if level_type == '◎':
        return ['◎', '○']
    elif level_type == '〇':
        return ['◎', '○']
    elif level_type == '△':
        return ['◎', '○', '△']
    elif level_type == '〇のみ':
        return ['○']
    elif level_type == '△のみ':
        return ['△']
    else:
        # 全レベルを対象とする
        return ['◎', '○', '△']

def find_days_by_single_level(df, members, level_type):
    """
    指定メンバー全員が同じレベルで揃う日を抽出する
    level_type: '◎', '〇', '△', '〇のみ', '△のみ'
    """
    ok_symbols = get_ok_symbols(level_type)
    selected_df = df[members]

    # 参加可能記号リストにハイフンも追加
    ok_symbols_with_dash = ok_symbols + ['-']

    # 全員が参加可能記号かハイフンであるかを判定
    all_ok_or_dash = selected_df.isin(ok_symbols_with_dash).all(axis=1)

    # 日付列を含めて結果を返す
    return df[all_ok_or_dash][['日付'] + members]

def create_attendees_count_table(df):
    """各日の参加可能人数（5レベル）の表を作成する"""
    members_df = df.loc[:, df.columns != '日付']
    count_ok_only = (members_df == '◎').sum(axis=1)
    count_ok_night_undecided = members_df.isin(['◎', '○']).sum(axis=1)
    count_ok_day_undecided = members_df.isin(['◎', '△']).sum(axis=1)
    count_night_only = (members_df == '○').sum(axis=1)
    count_day_only = (members_df == '△').sum(axis=1)
    result_df = pd.DataFrame({
        '日付': df['日付'],
        '◎-': count_ok_only,
        '◎〇-': count_ok_night_undecided,
        '◎△-': count_ok_day_undecided,
        '〇-のみ': count_night_only,
        '△-のみ': count_day_only
    })
    return result_df

# ファイルの文字コードを判別するヘルパー関数
def detect_encoding(file_obj):
    raw_data = file_obj.read(10000)
    result = chardet.detect(raw_data)
    file_obj.seek(0)
    return result['encoding']

# --- Flaskのルート定義 ---
@app.route('/', methods=['GET'])
def index():
    members = session.get('members', [])
    result_table_html = session.get('result_table_html', "")
    meta_data_json = session.get('meta_data_json', "{}")
    meta_data = json.loads(meta_data_json)

    # メンバー指定の抽出結果は表示後にクリア
    session.pop('result_table_html', None)
    session.pop('meta_data_json', None)

    # データベースから全データを取得
    with app.app_context():
        attendees = Attendee.query.all()
        # メンバーリストをデータベースから動的に取得
        if attendees:
            members_unsorted = list(set([a.member for a in attendees]))

            # メンバーを英字、ひらがな、漢字の順にソートする
            members_en = sorted([m for m in members_unsorted if 'a' <= m[0].lower() <= 'z'])
            members_hiragana = sorted([m for m in members_unsorted if 'ぁ' <= m[0] <= 'ん'])
            members_kanji = sorted([m for m in members_unsorted if '一' <= m[0] <= '龯'])

            members = members_en + members_hiragana + members_kanji
        else:
            members = []

    # DataFrameに変換し、重複を削除
    data = [{'日付': a.date, 'メンバー': a.member, '記号': a.status} for a in attendees]
    df = pd.DataFrame(data).drop_duplicates()

    # データが存在する場合のみ表を作成
    if not df.empty:
        # 日付でデータを一つにまとめるためにピボット
        pivot_df = df.pivot_table(index='日付', columns='メンバー', values='記号', aggfunc='first')

        # 日付のインデックスを時系列順に並び替え
        pivot_df = pivot_df.sort_index(key=lambda x: pd.to_datetime(x.str.split('(').str[0], format='%m/%d', errors='coerce'), axis=0)

        # 全てのユニークな月を取得
        months = sorted(list(set([int(d.split('(')[0].split('/')[0]) for d in pivot_df.index])))

        # 現在の月をURLパラメータから取得、なければ最初の月
        current_month = int(request.args.get('month', months[0] if months else None))

        # 選択された月に合わせてデータをフィルタリング
        pivot_df = pivot_df[pivot_df.index.str.split('(').str[0].str.split('/').str[0] == str(current_month)]

        # 参加可能人数の表を生成
        attendees_table = create_attendees_count_table(pivot_df.reset_index())
        attendees_table_html = attendees_table.rename(columns={'◎-': '◎終日', '◎〇-': '◎〇夜', '◎△-': '◎△昼', '〇-のみ': '〇夜のみ', '△-のみ': '△昼のみ'}).to_html(classes='data', header="true", index=False)
        session['attendees_table_html'] = attendees_table_html
    else:
        attendees_table_html = ""
        months = []
        current_month = None
        session['attendees_table_html'] = ""

    # セッションから選択状態を取得
    selected_members = session.get('selected_members', [])
    current_level = session.get('current_level', '◎')

    return render_template('index.html',
        members=members,
        attendees_table_html=attendees_table_html,
        result_table_html=result_table_html,
        meta_data=meta_data,
        months=months,
        current_month=current_month,
        selected_members=selected_members,
        current_level=current_level)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'csv_file' not in request.files:
        return 'ファイルがありません'
    file = request.files['csv_file']
    if file.filename == '':
        return 'ファイルが選択されていません'

    try:
        # CSVファイルの読み込み
        encoding = chardet.detect(file.read())['encoding']
        file.seek(0)
        df = pd.read_csv(file, encoding=encoding, header=None, skip_blank_lines=True)

        # メンバーリストを取得（1行目）
        members = df.iloc[0, 1:].tolist()

        # データベースを一度クリア
        with app.app_context():
            db.session.query(Attendee).delete()
            db.session.commit()

        # データをDataFrameに変換
        data_to_save = []
        for row_index, row in df.iloc[1:-2].iterrows():
            date = str(row.iloc[0]).strip()
            for member_index, member_name in enumerate(members):
                status = str(row.iloc[member_index + 1]).strip()
                data_to_save.append({
                    'date': date,
                    'member': member_name,
                    'status': status
                })

        # DataFrameに変換して重複を削除
        df_to_save = pd.DataFrame(data_to_save).drop_duplicates()

        # データベースに保存
        with app.app_context():
            for index, row in df_to_save.iterrows():
                attendee = Attendee(date=row['date'], member=row['member'], status=row['status'])
                db.session.add(attendee)
            db.session.commit()

        # メンバーリストをセッションに保存
        session['members'] = members

        # トップページにリダイレクト
        return redirect(url_for('index'))

    except Exception as e:
        return f"エラーが発生しました: {e}"

@app.route('/extract', methods=['POST'])
def extract_data():
    selected_members = request.form.getlist('members')
    level_single = request.form.get('level_single')

    if not selected_members or not level_single:
        # 選択状態をセッションに保存
        session['selected_members'] = selected_members
        session['current_level'] = level_single
        return redirect(url_for('index'))

    try:
        # データベースからデータを読み込む
        with app.app_context():
            attendees = Attendee.query.all()

        # DataFrameに変換
        data = [{'日付': a.date, 'メンバー': a.member, '記号': a.status} for a in attendees]
        df = pd.DataFrame(data)

        # データをピボットしてCSVと同じ形式にする
        pivot_df = df.pivot_table(index='日付', columns='メンバー', values='記号', aggfunc='first')

        # 日付のインデックスを時系列順に並び替え
        pivot_df = pivot_df.sort_index(key=lambda x: pd.to_datetime(x.str.split('(').str[0], format='%m/%d', errors='coerce'), axis=0)

        # メンバーリストを再取得（カラム順を合わせるため）
        members = list(pivot_df.columns)

        # メンバーリストをセッションに保存
        session['members'] = members

        # メンバー抽出とメタデータの処理
        ok_symbols = get_ok_symbols(level_single)
        selected_df = pivot_df[selected_members]
        all_ok = selected_df.isin(ok_symbols).all(axis=1)
        result_df = pivot_df[all_ok][selected_members]

        result_table_html = result_df.rename_axis(None, axis=1).reset_index().to_html(classes='data', header=True, index=False)
        session['result_table_html'] = result_table_html

        # セッションに選択状態を保存
        session['selected_members'] = selected_members
        session['current_level'] = level_single

        # メタデータは新しいシステムではまだ扱わないのでクリアする
        session.pop('meta_data_json', None)

        return redirect(url_for('index'))

    except Exception as e:
        session['result_table_html'] = f"エラーが発生しました: {e}"
        return redirect(url_for('index'))

# --- サーバー起動とデータベース作成 ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)