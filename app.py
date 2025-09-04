import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, session, flash, get_flashed_messages
from flask_sqlalchemy import SQLAlchemy
import chardet
import os
import json
import csv
from io import StringIO
from datetime import datetime

# --- Flask Webアプリのメイン部分 ---
app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['UPLOAD_FOLDER'] = 'uploads'

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

class Metadata(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    member = db.Column(db.String(50), nullable=False)
    key = db.Column(db.String(50), nullable=False)
    value = db.Column(db.String(255), nullable=False)

    __table_args__ = (db.UniqueConstraint('member', 'key', name='unique_member_key'),)

    def __repr__(self):
        return f'<Metadata member={self.member} key={self.key} value={self.value}>'

# --- 共通のヘルパー関数 ---
def get_ok_symbols(level_type):
    if level_type == '◎':
        return ['◎', '-']
    elif level_type == '〇':
        return ['◎', '○', '-']
    elif level_type == '△':
        return ['◎', '△', '-']
    elif level_type == '〇のみ':
        return ['○', '-']
    elif level_type == '△のみ':
        return ['△', '-']
    elif level_type == '全件':
        return ['◎', '○', '△', '×', '-']
    else:
        return ['◎', '○', '△', '-']

def create_attendees_count_table(df):
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

# --- Flaskのルート定義 ---
@app.route('/', methods=['GET'])
def index():
    selected_members = session.get('selected_members', [])
    current_level = session.get('current_level', '◎')
    
    with app.app_context():
        attendees = Attendee.query.all()
        # メンバーごとに最終更新日時とコメントを取得
        metadata_dict = {
            member: {
                '最終更新日時': '',
                'コメント': ''
            } for member in selected_members
        }
        for m in Metadata.query.filter(Metadata.member.in_(selected_members)).all():
            metadata_dict[m.member][m.key] = m.value

        if attendees:
            members_unsorted = list(set([a.member for a in attendees]))
            members_en = sorted([m for m in members_unsorted if m and 'a' <= m[0].lower() <= 'z'])
            members_kana_hira = sorted([m for m in members_unsorted if m and ('ぁ' <= m[0] <= 'ん' or 'ァ' <= m[0] <= 'ン')])
            members_kanji = sorted([m for m in members_unsorted if m and '一' <= m[0] <= '龯'])
            members = members_en + members_kana_hira + members_kanji
        else:
            members = []

    data = [{'日付': a.date, 'メンバー': a.member, '記号': a.status} for a in attendees]
    df = pd.DataFrame(data).drop_duplicates()

    attendees_table_html = ""
    result_table_html = ""
    months = []
    current_month = None

    if not df.empty:
        pivot_df = df.pivot_table(index='日付', columns='メンバー', values='記号', aggfunc='first')
        pivot_df = pivot_df.sort_index(key=lambda x: pd.to_datetime(x.str.split('(').str[0], format='%m/%d', errors='coerce'), axis=0)
        
        valid_dates = [d for d in pivot_df.index if d.strip() and '/' in d]
        months = sorted(list(set([int(d.split('(')[0].split('/')[0]) for d in valid_dates])))
        
        current_month = int(request.args.get('month', months[0] if months else None))
        filtered_pivot_df = pivot_df[pivot_df.index.str.split('(').str[0].str.split('/').str[0] == str(current_month)]

        attendees_table = create_attendees_count_table(filtered_pivot_df.reset_index())
        attendees_table_html = attendees_table.rename(columns={'◎-': '終日', '◎〇-': '夜', '◎△-': '昼', '〇-のみ': '夜のみ', '△-のみ': '昼のみ'}).to_html(classes='data', header="true", index=False)

        if selected_members:
            ok_symbols = get_ok_symbols(current_level)
            selected_df = filtered_pivot_df[selected_members]
            all_ok = selected_df.isin(ok_symbols).all(axis=1)
            result_df = filtered_pivot_df[all_ok][selected_members]
            result_table_html = result_df.rename_axis(None, axis=1).reset_index().to_html(classes='data', header=True, index=False)

    return render_template('index.html',
        members=members,
        attendees_table_html=attendees_table_html,
        result_table_html=result_table_html,
        months=months,
        current_month=current_month,
        selected_members=selected_members,
        current_level=current_level,
        metadata_dict=metadata_dict,
        messages=get_flashed_messages(with_categories=True))


@app.route('/upload', methods=['POST'])
def upload_file():
    if 'csv_file' not in request.files:
        flash('ファイルがありません', 'danger')
        return redirect(url_for('index'))
    file = request.files['csv_file']
    if file.filename == '':
        flash('ファイルが選択されていません', 'danger')
        return redirect(url_for('index'))

    try:
        raw_data = file.read()
        result = chardet.detect(raw_data)
        encoding = result['encoding'] if result['encoding'] else 'utf-8'
        file_string = raw_data.decode(encoding, errors='replace')
        
        csv_reader = csv.reader(StringIO(file_string))
        
        rows = list(csv_reader)
        if not rows:
            flash('CSVファイルが空です。', 'danger')
            return redirect(url_for('index'))
            
        header_row = rows[0]
        members = [col.strip() for col in header_row[1:] if col.strip()]

        data_rows = rows[1:-2]
        metadata_rows = rows[-2:]
        
        last_update_row = metadata_rows[0] if len(metadata_rows) > 0 else None
        comment_row = metadata_rows[1] if len(metadata_rows) > 1 else None

        with app.app_context():
            db.session.query(Attendee).delete()
            db.session.query(Metadata).delete()
            db.session.commit()

        data_to_save = []
        for row in data_rows:
            if not row or not row[0].strip():
                continue
            
            try:
                date_str = row[0].strip().split('(')[0]
                pd.to_datetime(date_str, format='%m/%d', errors='raise')
                date = row[0].strip()
            except (ValueError, IndexError):
                continue

            for member_index, member_name in enumerate(members):
                if member_index + 1 >= len(row):
                    continue
                status = row[member_index + 1].strip()
                data_to_save.append({
                    'date': date,
                    'member': member_name,
                    'status': status
                })

        with app.app_context():
            df_to_save = pd.DataFrame(data_to_save).drop_duplicates()
            for index, row in df_to_save.iterrows():
                attendee = Attendee(date=row['date'], member=row['member'], status=row['status'])
                db.session.add(attendee)

            for member_index, member_name in enumerate(members):
                if last_update_row and len(last_update_row) > member_index + 1:
                    last_update_str = last_update_row[member_index + 1].strip()
                    if last_update_str:
                        metadata = Metadata(member=member_name, key='最終更新日時', value=last_update_str)
                        db.session.add(metadata)
                
                if comment_row and len(comment_row) > member_index + 1:
                    comment_str = comment_row[member_index + 1].strip()
                    if comment_str:
                        metadata = Metadata(member=member_name, key='コメント', value=comment_str)
                        db.session.add(metadata)
            
            db.session.commit()

        return redirect(url_for('index'))

    except Exception as e:
        flash(f"エラーが発生しました: {e}", 'danger')
        return redirect(url_for('index'))


@app.route('/extract', methods=['POST'])
def extract_data():
    session['selected_members'] = request.form.getlist('members')
    session['current_level'] = request.form.get('level_single')
    return redirect(url_for('index'))


# --- サーバー起動とデータベース作成 ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)