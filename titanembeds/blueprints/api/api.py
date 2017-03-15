from titanembeds.database import db, Guilds, UnauthenticatedUsers, UnauthenticatedBans
from titanembeds.decorators import valid_session_required
from flask import Blueprint, abort, jsonify, session, request
from sqlalchemy import and_
from werkzeug.contrib.cache import SimpleCache
import random
import requests
import json
import time
from config import config

api = Blueprint("api", __name__)
cache = SimpleCache()

_DISCORD_API_BASE = "https://discordapp.com/api/v6"

def user_unauthenticated():
    if 'unauthenticated' in session:
        return session['unauthenticated']
    return True

def checkUserRevoke(guild_id, user_key=None):
    revoked = True #guilty until proven not revoked
    if user_unauthenticated():
        dbUser = UnauthenticatedUsers.query.filter(and_(UnauthenticatedUsers.guild_id == guild_id, UnauthenticatedUsers.user_key == user_key)).first()
        revoked = dbUser.isRevoked()
    else:
        pass # Todo: handle authenticated user revocation status
    return revoked

def checkUserBanned(guild_id, ip_address=None):
    banned = True
    if user_unauthenticated():
        dbUser = UnauthenticatedBans.query.filter(and_(UnauthenticatedBans.guild_id == guild_id, UnauthenticatedBans.ip_address == ip_address)).all()
        if not dbUser:
            banned = False
        else:
            for usr in dbUser:
                if usr.lifter_id is not None:
                    banned = False
    else:
        pass #todo: handle authenticated user banned status
    return banned

def get_client_ipaddr():
    if hasattr(request.headers, "X-Real-IP"): # pythonanywhere specific
        return request.headers['X-Real-IP']
    else: # general
        return request.remote_addr

def get_all_guilds():
    _endpoint = _DISCORD_API_BASE + "/users/@me/guilds"
    payload = {}
    guilds = []
    headers = {'Authorization': 'Bot ' + config['bot-token']}
    count = 1 #priming the loop
    last_guild = ""
    while count > 0:
        r = requests.get(_endpoint, params=payload, headers=headers)
        js = r.json()
        if r.status_code == 200:
            count = len(js)
            guilds.extend(js)
            if count > 0:
                payload['after'] = js[-1]['id']
        else:
            time.sleep(js['retry_after'] / float(1000))
        return guilds

def check_guild_existance(guild_id):
    dbGuild = Guild.query.filter_by(guild_id=guild_id).first()
    if not dbGuild:
        return False
    guilds = cache.get('bot_guilds')
    if guilds is None:
        guilds = get_all_guilds()
        cache.set('bot_guilds', guilds)
    for guild in guilds:
        if guild_id == guild['id']:
            return True
    return False

def update_user_status(guild_id, username, user_key=None):
    if user_unauthenticated():
        ip_address = get_client_ipaddr()
        status = {
            'ip_address': ip_address,
            'username': username,
            'user_key': user_key,
            'guild_id': guild_id,
            'banned': checkUserBanned(guild_id, ip_address),
            'revoked': checkUserRevoke(guild_id, user_key),
        }
        if status['banned'] or status['revoked']:
            return status
        dbUser = UnauthenticatedUsers.query.filter(and_(UnauthenticatedUsers.guild_id == guild_id, UnauthenticatedUsers.user_key == user_key)).first()
        dbUser.bumpTimestamp()
        if dbUser.username != username or dbUser.ip_address != ip_address:
            dbUser.username = username
            dbUser.ip_address = ip_address
            db.session.commit()
    else:
        pass #authenticated user todo
    return status

def get_channel_messages(channel_id, after_snowflake=None):
    _endpoint = _DISCORD_API_BASE + "/channels/{channel_id}/messages".format(channel_id=channel_id)
    payload = {}
    if after_snowflake is not None:
        payload = {'after': after_snowflake}
    headers = {'Authorization': 'Bot ' + config['bot-token']}
    r = requests.get(_endpoint, params=payload, headers=headers)
    return json.loads(r.content)

def post_create_message(channel_id, content):
    _endpoint = _DISCORD_API_BASE + "/channels/{channel_id}/messages".format(channel_id=channel_id)
    payload = {'content': session['username'] + ": " + content}
    headers = {'Authorization': 'Bot ' + config['bot-token'], 'Content-Type': 'application/json'}
    r = requests.post(_endpoint, headers=headers, data=json.dumps(payload))
    return json.loads(r.content)

@api.route("/fetch", methods=["GET"])
@valid_session_required(api=True)
def fetch():
    channel_id = request.args.get('channel_id')
    after_snowflake = request.args.get('after', None, type=int)
    if user_unauthenticated():
        key = session['user_keys'][channel_id]
    else:
        key = None
    status = update_user_status(channel_id, session['username'], key)
    if status['banned'] or status['revoked']:
        messages = {}
    else:
        messages = get_channel_messages(channel_id, after_snowflake)
    return jsonify(messages=messages, status=status)

@api.route("/post", methods=["POST"])
@valid_session_required(api=True)
def post():
    channel_id = request.form.get('channel_id')
    content = request.form.get('content')
    if user_unauthenticated():
        key = session['user_keys'][channel_id]
    else:
        key = None
    status = update_user_status(channel_id, session['username'], key)
    if status['banned'] or status['revoked']:
        return jsonify(status=status)
    message = post_create_message(channel_id, content)
    return jsonify(message=message, status=status)

@api.route("/create_unauthenticated_user", methods=["POST"])
def create_unauthenticated_user():
    session['unauthenticated'] = True
    username = request.form['username']
    guild_id = request.form['guild_id']
    ip_address = get_client_ipaddr()
    if not check_guild_existance(guild_id):
        abort(404)
    if not checkUserBanned(guild_id, ip_address):
        session['username'] = username
        if 'user_id' not in session:
            session['user_id'] = random.randint(0,9999)
        user = UnauthenticatedUsers(guild_id, username, session['user_id'], ip_address)
        db.session.add(user)
        db.session.commit()
        key = user.user_key
        if 'user_keys' not in session:
            session['user_keys'] = {guild_id: key}
        else:
            session['user_keys'][guild_id] = key
        status = update_user_status(guild_id, username, key)
        return jsonify(status=status)
    else:
        status = {'banned': True}
        return jsonify(status=status)

@api.route("/query_guild", methods=["GET"])
@valid_session_required(api=True)
def query_guild():
    guild_id = request.args.get('guild_id')
    return jsonify(exists=check_guild_existance(guild_id))