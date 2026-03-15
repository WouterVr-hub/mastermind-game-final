import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import random
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'a-simple-and-working-secret-key-finally'
socketio = SocketIO(app, async_mode='eventlet')

# --- Constants & Game State ---
SECRET_COLORS = ["red", "blue", "green", "yellow", "black", "white"]
GUESS_OPTIONS = SECRET_COLORS + ["empty"]
CODE_LENGTH = 5
NUM_COLOR_PEGS = 4

class GameState:
    def __init__(self):
        self.players = {}
        self.game_started = False
        self.player_order = []
        self.current_turn_sid = None
        self.host_sid = None
        self.guesses = []
        self.secret_code = []
        print("--- New, Clean GameState created. Server is ready. ---")

    def get_player_list_data(self):
        # FIX for [object Object]: Send a list of objects, not strings.
        return [{"name": data["name"]} for data in self.players.values()]

    def reset_board(self):
        for player_data in self.players.values():
            player_data.pop("secret", None)
            player_data.pop("eliminated", None)
        self.game_started = False
        self.current_turn_sid = None
        self.player_order = []
        self.guesses = []
        self.secret_code = []
        print("--- Game board has been reset. ---")

GAME = GameState()

# --- Routes and Socket Handlers ---
@app.route('/')
def index():
    return render_template('index.html')

# STABILITY FIX: Handler for the client's keep-alive ping.
@socketio.on('client_ping')
def handle_client_ping():
    # This function intentionally does nothing. Its only purpose is to receive
    # an event to keep the Render service from going idle.
    pass

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")
    emit('color_list', {'colors': GUESS_OPTIONS})
    if GAME.game_started:
        emit('game_in_progress')

@socketio.on('disconnect')
def handle_disconnect():
    global GAME
    if request.sid in GAME.players:
        player_name = GAME.players.pop(request.sid).get("name", "A player")
        print(f"Player '{player_name}' disconnected.")
        if request.sid == GAME.host_sid:
            print("Host disconnected. Full server reset."); GAME = GameState()
            emit('game_reset_full', {'message': 'The Host has disconnected. The game has been fully reset.'}, broadcast=True)
        else:
            if GAME.game_started:
                GAME.reset_board()
                emit('game_reset_board', {'message': f'{player_name} left. The game board has been reset.'}, broadcast=True)
            emit('update_player_list', {'players': GAME.get_player_list_data()}, broadcast=True)

@socketio.on('register_player')
def handle_register(data):
    if GAME.game_started: return
    sid = request.sid; name = data.get('name', f'Player_{sid[:4]}'); is_host = not GAME.host_sid
    if is_host:
        GAME.host_sid = sid; name += " (Host)"
    GAME.players[sid] = {"name": name, "is_host": is_host}
    emit('is_host', {'is_host': is_host})
    emit('update_player_list', {'players': GAME.get_player_list_data()}, broadcast=True)

@socketio.on('start_game')
def handle_start_game():
    if request.sid != GAME.host_sid or GAME.game_started: return
    actual_players_sids = [sid for sid, p_data in GAME.players.items() if not p_data["is_host"]]
    if len(actual_players_sids) < 2: return emit('error', {'message': 'Need at least 2 players to start.'})
    GAME.game_started = True
    GAME.secret_code = random.sample(SECRET_COLORS, NUM_COLOR_PEGS) + ['empty']; random.shuffle(GAME.secret_code)
    print(f"Secret code created: {GAME.secret_code}")
    color_positions = [i for i, color in enumerate(GAME.secret_code) if color != 'empty']; random.shuffle(color_positions)
    for i, player_sid in enumerate(actual_players_sids):
        if i < len(color_positions):
            pos_index = color_positions[i]; secret = {"pos": pos_index + 1, "color": GAME.secret_code[pos_index]}
            GAME.players[player_sid]["secret"] = secret; emit('your_secret', secret, room=player_sid)
    GAME.player_order = actual_players_sids; random.shuffle(GAME.player_order)
    GAME.current_turn_sid = GAME.player_order[0]; current_player_name = GAME.players[GAME.current_turn_sid]["name"]
    emit('host_overview', {'secret_code': GAME.secret_code}, room=GAME.host_sid)
    emit('game_started', {'turn': current_player_name}, broadcast=True)

@socketio.on('submit_guess')
def handle_guess(data):
    sid = request.sid
    if sid != GAME.current_turn_sid: return
    guess = data.get('guess')
    if not isinstance(guess, list) or len(guess) != CODE_LENGTH: return
    guesser_name = GAME.players[sid]["name"]; temp_secret = list(GAME.secret_code); temp_guess = list(guess); feedback = []
    for i in range(CODE_LENGTH):
        if temp_secret[i] != 'empty' and temp_secret[i] == temp_guess[i]:
            feedback.append('black'); temp_secret[i] = None; temp_guess[i] = None
    for i in range(CODE_LENGTH):
        if temp_guess[i] is not None and temp_guess[i] != 'empty' and temp_guess[i] in temp_secret:
            feedback.append('white'); temp_secret.remove(temp_guess[i])
    random.shuffle(feedback); GAME.guesses.append({"guesser": guesser_name, "guess": guess, "feedback": feedback})
    if data.get('is_final'):
        is_winner = feedback.count('black') == NUM_COLOR_PEGS and len(feedback) == NUM_COLOR_PEGS
        if is_winner:
            emit('game_over', {'winner': guesser_name, 'secret_code': GAME.secret_code}, broadcast=True)
            GAME.reset_board(); return
        else:
            GAME.players[sid]['eliminated'] = True; emit('eliminated', {'name': guesser_name}, broadcast=True)
    try:
        current_idx = GAME.player_order.index(sid)
        for i in range(1, len(GAME.player_order) + 1):
            next_sid_candidate = GAME.player_order[(current_idx + i) % len(GAME.player_order)]
            if not GAME.players[next_sid_candidate].get("eliminated"):
                GAME.current_turn_sid = next_sid_candidate
                emit('new_turn', {'last_guess': GAME.guesses[-1], 'next_turn': GAME.players[GAME.current_turn_sid]["name"]}, broadcast=True)
                return
        emit('game_over', {'winner': None, 'message': 'All players have been eliminated!'}, broadcast=True)
        GAME.reset_board()
    except (ValueError, IndexError): emit('error', {'message': 'Error finding next player.'})

# This block is not used by Render but is good for local testing.
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)# No monkey_patching is needed for the gevent setup
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import random
import os

# Create the Flask app instance first
app = Flask(__name__)
app.config['SECRET_KEY'] = 'a-simple-and-working-secret-key-finally'

# Initialize SocketIO with gevent as the async_mode and attach it to the app
socketio = SocketIO(app, async_mode='gevent')

# --- Constants and Game State Class ---
SECRET_COLORS = ["red", "blue", "green", "yellow", "black", "white"]
GUESS_OPTIONS = SECRET_COLORS + ["empty"]
CODE_LENGTH = 5; NUM_COLOR_PEGS = 4

class GameState:
    def __init__(self):
        self.players = {}; self.game_started = False; self.player_order = []
        self.current_turn_sid = None; self.host_sid = None; self.guesses = []
        self.secret_code = []; print("--- New, Clean GameState created. Server is ready. ---")

    def get_player_list_data(self):
        # FIX for [object Object]: Send a list of objects, not strings.
        return [{"name": data["name"]} for data in self.players.values()]

    def reset_board(self):
        for player_data in self.players.values():
            player_data.pop("secret", None); player_data.pop("eliminated", None)
        self.game_started = False; self.current_turn_sid = None
        self.player_order = []; self.guesses = []; self.secret_code = []
        print("--- Game board has been reset. ---")

GAME = GameState()

# --- Routes and SocketIO Event Handlers ---
@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('client_ping')
def handle_client_ping():
    pass # This keeps the connection alive on Render's free tier

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}"); emit('color_list', {'colors': GUESS_OPTIONS})
    if GAME.game_started: emit('game_in_progress')

@socketio.on('disconnect')
def handle_disconnect():
    global GAME
    if request.sid in GAME.players:
        player_name = GAME.players.pop(request.sid).get("name", "A player")
        print(f"Player '{player_name}' disconnected.")
        if request.sid == GAME.host_sid:
            print("Host disconnected. Full server reset."); GAME = GameState()
            emit('game_reset_full', {'message': 'The Host has disconnected.'}, broadcast=True)
        else:
            if GAME.game_started:
                GAME.reset_board(); emit('game_reset_board', {'message': f'{player_name} left.'}, broadcast=True)
            emit('update_player_list', {'players': GAME.get_player_list_data()}, broadcast=True)

@socketio.on('register_player')
def handle_register(data):
    if GAME.game_started: return
    sid = request.sid; name = data.get('name', f'Player_{sid[:4]}'); is_host = not GAME.host_sid
    if is_host:
        GAME.host_sid = sid; name += " (Host)"
    GAME.players[sid] = {"name": name, "is_host": is_host}
    emit('is_host', {'is_host': is_host}); emit('update_player_list', {'players': GAME.get_player_list_data()}, broadcast=True)

@socketio.on('start_game')
def handle_start_game():
    if request.sid != GAME.host_sid or GAME.game_started: return
    actual_players_sids = [sid for sid, p_data in GAME.players.items() if not p_data["is_host"]]
    if len(actual_players_sids) < 2: return emit('error', {'message': 'Need at least 2 players to start.'})
    GAME.game_started = True; GAME.secret_code = random.sample(SECRET_COLORS, NUM_COLOR_PEGS) + ['empty']; random.shuffle(GAME.secret_code)
    color_positions = [i for i, color in enumerate(GAME.secret_code) if color != 'empty']; random.shuffle(color_positions)
    for i, player_sid in enumerate(actual_players_sids):
        if i < len(color_positions):
            pos_index = color_positions[i]; secret = {"pos": pos_index + 1, "color": GAME.secret_code[pos_index]}
            GAME.players[player_sid]["secret"] = secret; emit('your_secret', secret, room=player_sid)
    GAME.player_order = actual_players_sids; random.shuffle(GAME.player_order)
    GAME.current_turn_sid = GAME.player_order[0]; current_player_name = GAME.players[GAME.current_turn_sid]["name"]
    emit('host_overview', {'secret_code': GAME.secret_code}, room=GAME.host_sid); emit('game_started', {'turn': current_player_name}, broadcast=True)

@socketio.on('submit_guess')
def handle_guess(data):
    sid = request.sid
    if sid != GAME.current_turn_sid: return
    guess = data.get('guess')
    if not isinstance(guess, list) or len(guess) != CODE_LENGTH: return
    guesser_name = GAME.players[sid]["name"]; temp_secret = list(GAME.secret_code); temp_guess = list(guess); feedback = []
    for i in range(CODE_LENGTH):
        if temp_secret[i] != 'empty' and temp_secret[i] == temp_guess[i]:
            feedback.append('black'); temp_secret[i] = None; temp_guess[i] = None
    for i in range(CODE_LENGTH):
        if temp_guess[i] is not None and temp_guess[i] != 'empty' and temp_guess[i] in temp_secret:
            feedback.append('white'); temp_secret.remove(temp_guess[i])
    random.shuffle(feedback); GAME.guesses.append({"guesser": guesser_name, "guess": guess, "feedback": feedback})
    if data.get('is_final'):
        is_winner = feedback.count('black') == NUM_COLOR_PEGS and len(feedback) == NUM_COLOR_PEGS
        if is_winner:
            emit('game_over', {'winner': guesser_name, 'secret_code': GAME.secret_code}, broadcast=True); GAME.reset_board(); return
        else:
            GAME.players[sid]['eliminated'] = True; emit('eliminated', {'name': guesser_name}, broadcast=True)
    try:
        current_idx = GAME.player_order.index(sid)
        for i in range(1, len(GAME.player_order) + 1):
            next_sid_candidate = GAME.player_order[(current_idx + i) % len(GAME.player_order)]
            if not GAME.players[next_sid_candidate].get("eliminated"):
                GAME.current_turn_sid = next_sid_candidate
                emit('new_turn', {'last_guess': GAME.guesses[-1], 'next_turn': GAME.players[GAME.current_turn_sid]["name"]}, broadcast=True); return
        emit('game_over', {'winner': None, 'message': 'All players eliminated!'}, broadcast=True); GAME.reset_board()
    except (ValueError, IndexError): emit('error', {'message': 'Error finding next player.'})

# The if __name__ == '__main__' block is NOT used by Gunicorn, but is helpful for local testing
if __name__ == '__main__':
    socketio.run(app, debug=True)# No monkey_patching is needed for the gevent setup
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import random
import os

# Create the Flask app instance first
app = Flask(__name__)
app.config['SECRET_KEY'] = 'a-simple-and-working-secret-key-finally'

# Initialize SocketIO with gevent as the async_mode and attach it to the app
socketio = SocketIO(app, async_mode='gevent')

# --- Constants and Game State Class ---
SECRET_COLORS = ["red", "blue", "green", "yellow", "black", "white"]
GUESS_OPTIONS = SECRET_COLORS + ["empty"]
CODE_LENGTH = 5; NUM_COLOR_PEGS = 4

class GameState:
    def __init__(self):
        self.players = {}; self.game_started = False; self.player_order = []
        self.current_turn_sid = None; self.host_sid = None; self.guesses = []
        self.secret_code = []; print("--- New, Clean GameState created. Server is ready. ---")

    def get_player_list_data(self):
        # FIX for [object Object]: Send a list of objects, not strings.
        return [{"name": data["name"]} for data in self.players.values()]

    def reset_board(self):
        for player_data in self.players.values():
            player_data.pop("secret", None); player_data.pop("eliminated", None)
        self.game_started = False; self.current_turn_sid = None
        self.player_order = []; self.guesses = []; self.secret_code = []
        print("--- Game board has been reset. ---")

GAME = GameState()

# --- Routes and SocketIO Event Handlers ---
@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('client_ping')
def handle_client_ping():
    pass # This keeps the connection alive on Render's free tier

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}"); emit('color_list', {'colors': GUESS_OPTIONS})
    if GAME.game_started: emit('game_in_progress')

@socketio.on('disconnect')
def handle_disconnect():
    global GAME
    if request.sid in GAME.players:
        player_name = GAME.players.pop(request.sid).get("name", "A player")
        print(f"Player '{player_name}' disconnected.")
        if request.sid == GAME.host_sid:
            print("Host disconnected. Full server reset."); GAME = GameState()
            emit('game_reset_full', {'message': 'The Host has disconnected.'}, broadcast=True)
        else:
            if GAME.game_started:
                GAME.reset_board(); emit('game_reset_board', {'message': f'{player_name} left.'}, broadcast=True)
            emit('update_player_list', {'players': GAME.get_player_list_data()}, broadcast=True)

@socketio.on('register_player')
def handle_register(data):
    if GAME.game_started: return
    sid = request.sid; name = data.get('name', f'Player_{sid[:4]}'); is_host = not GAME.host_sid
    if is_host:
        GAME.host_sid = sid; name += " (Host)"
    GAME.players[sid] = {"name": name, "is_host": is_host}
    emit('is_host', {'is_host': is_host}); emit('update_player_list', {'players': GAME.get_player_list_data()}, broadcast=True)

@socketio.on('start_game')
def handle_start_game():
    if request.sid != GAME.host_sid or GAME.game_started: return
    actual_players_sids = [sid for sid, p_data in GAME.players.items() if not p_data["is_host"]]
    if len(actual_players_sids) < 2: return emit('error', {'message': 'Need at least 2 players to start.'})
    GAME.game_started = True; GAME.secret_code = random.sample(SECRET_COLORS, NUM_COLOR_PEGS) + ['empty']; random.shuffle(GAME.secret_code)
    color_positions = [i for i, color in enumerate(GAME.secret_code) if color != 'empty']; random.shuffle(color_positions)
    for i, player_sid in enumerate(actual_players_sids):
        if i < len(color_positions):
            pos_index = color_positions[i]; secret = {"pos": pos_index + 1, "color": GAME.secret_code[pos_index]}
            GAME.players[player_sid]["secret"] = secret; emit('your_secret', secret, room=player_sid)
    GAME.player_order = actual_players_sids; random.shuffle(GAME.player_order)
    GAME.current_turn_sid = GAME.player_order[0]; current_player_name = GAME.players[GAME.current_turn_sid]["name"]
    emit('host_overview', {'secret_code': GAME.secret_code}, room=GAME.host_sid); emit('game_started', {'turn': current_player_name}, broadcast=True)

@socketio.on('submit_guess')
def handle_guess(data):
    sid = request.sid
    if sid != GAME.current_turn_sid: return
    guess = data.get('guess')
    if not isinstance(guess, list) or len(guess) != CODE_LENGTH: return
    guesser_name = GAME.players[sid]["name"]; temp_secret = list(GAME.secret_code); temp_guess = list(guess); feedback = []
    for i in range(CODE_LENGTH):
        if temp_secret[i] != 'empty' and temp_secret[i] == temp_guess[i]:
            feedback.append('black'); temp_secret[i] = None; temp_guess[i] = None
    for i in range(CODE_LENGTH):
        if temp_guess[i] is not None and temp_guess[i] != 'empty' and temp_guess[i] in temp_secret:
            feedback.append('white'); temp_secret.remove(temp_guess[i])
    random.shuffle(feedback); GAME.guesses.append({"guesser": guesser_name, "guess": guess, "feedback": feedback})
    if data.get('is_final'):
        is_winner = feedback.count('black') == NUM_COLOR_PEGS and len(feedback) == NUM_COLOR_PEGS
        if is_winner:
            emit('game_over', {'winner': guesser_name, 'secret_code': GAME.secret_code}, broadcast=True); GAME.reset_board(); return
        else:
            GAME.players[sid]['eliminated'] = True; emit('eliminated', {'name': guesser_name}, broadcast=True)
    try:
        current_idx = GAME.player_order.index(sid)
        for i in range(1, len(GAME.player_order) + 1):
            next_sid_candidate = GAME.player_order[(current_idx + i) % len(GAME.player_order)]
            if not GAME.players[next_sid_candidate].get("eliminated"):
                GAME.current_turn_sid = next_sid_candidate
                emit('new_turn', {'last_guess': GAME.guesses[-1], 'next_turn': GAME.players[GAME.current_turn_sid]["name"]}, broadcast=True); return
        emit('game_over', {'winner': None, 'message': 'All players eliminated!'}, broadcast=True); GAME.reset_board()
    except (ValueError, IndexError): emit('error', {'message': 'Error finding next player.'})# No monkey_patching is needed for the gevent setup
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import random
import os

# Create the Flask app instance first
app = Flask(__name__)
app.config['SECRET_KEY'] = 'a-simple-and-working-secret-key-finally'

# Initialize SocketIO with gevent as the async_mode and attach it to the app
socketio = SocketIO(app, async_mode='gevent')

# --- Constants and Game State Class ---
SECRET_COLORS = ["red", "blue", "green", "yellow", "black", "white"]
GUESS_OPTIONS = SECRET_COLORS + ["empty"]
CODE_LENGTH = 5
NUM_COLOR_PEGS = 4

class GameState:
    def __init__(self):
        self.players = {}
        self.game_started = False
        self.player_order = []
        self.current_turn_sid = None
        self.host_sid = None
        self.guesses = []
        self.secret_code = []
        print("--- New, Clean GameState created. Server is ready. ---")

    def get_player_list_data(self):
        # FIX for [object Object]: Send a list of objects, not strings.
        return [{"name": data["name"]} for data in self.players.values()]

    def reset_board(self):
        for player_data in self.players.values():
            player_data.pop("secret", None)
            player_data.pop("eliminated", None)
        self.game_started = False; self.current_turn_sid = None
        self.player_order = []; self.guesses = []; self.secret_code = []
        print("--- Game board has been reset. ---")

GAME = GameState()

# --- Routes and SocketIO Event Handlers ---
@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('client_ping')
def handle_client_ping():
    pass # This keeps the connection alive on Render's free tier

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")
    emit('color_list', {'colors': GUESS_OPTIONS})
    if GAME.game_started: emit('game_in_progress')

@socketio.on('disconnect')
def handle_disconnect():
    global GAME
    if request.sid in GAME.players:
        player_name = GAME.players.pop(request.sid).get("name", "A player")
        print(f"Player '{player_name}' disconnected.")
        if request.sid == GAME.host_sid:
            print("Host disconnected. Full server reset."); GAME = GameState()
            emit('game_reset_full', {'message': 'The Host has disconnected.'}, broadcast=True)
        else:
            if GAME.game_started:
                GAME.reset_board()
                emit('game_reset_board', {'message': f'{player_name} left.'}, broadcast=True)
            emit('update_player_list', {'players': GAME.get_player_list_data()}, broadcast=True)

@socketio.on('register_player')
def handle_register(data):
    if GAME.game_started: return
    sid = request.sid; name = data.get('name', f'Player_{sid[:4]}'); is_host = not GAME.host_sid
    if is_host:
        GAME.host_sid = sid; name += " (Host)"
    GAME.players[sid] = {"name": name, "is_host": is_host}
    emit('is_host', {'is_host': is_host})
    emit('update_player_list', {'players': GAME.get_player_list_data()}, broadcast=True)

@socketio.on('start_game')
def handle_start_game():
    if request.sid != GAME.host_sid or GAME.game_started: return
    actual_players_sids = [sid for sid, p_data in GAME.players.items() if not p_data["is_host"]]
    if len(actual_players_sids) < 2: return emit('error', {'message': 'Need at least 2 players to start.'})
    GAME.game_started = True
    GAME.secret_code = random.sample(SECRET_COLORS, NUM_COLOR_PEGS) + ['empty']; random.shuffle(GAME.secret_code)
    color_positions = [i for i, color in enumerate(GAME.secret_code) if color != 'empty']; random.shuffle(color_positions)
    for i, player_sid in enumerate(actual_players_sids):
        if i < len(color_positions):
            pos_index = color_positions[i]; secret = {"pos": pos_index + 1, "color": GAME.secret_code[pos_index]}
            GAME.players[player_sid]["secret"] = secret; emit('your_secret', secret, room=player_sid)
    GAME.player_order = actual_players_sids; random.shuffle(GAME.player_order)
    GAME.current_turn_sid = GAME.player_order[0]; current_player_name = GAME.players[GAME.current_turn_sid]["name"]
    emit('host_overview', {'secret_code': GAME.secret_code}, room=GAME.host_sid)
    emit('game_started', {'turn': current_player_name}, broadcast=True)

@socketio.on('submit_guess')
def handle_guess(data):
    sid = request.sid
    if sid != GAME.current_turn_sid: return
    guess = data.get('guess')
    if not isinstance(guess, list) or len(guess) != CODE_LENGTH: return
    guesser_name = GAME.players[sid]["name"]; temp_secret = list(GAME.secret_code); temp_guess = list(guess); feedback = []
    for i in range(CODE_LENGTH):
        if temp_secret[i] != 'empty' and temp_secret[i] == temp_guess[i]:
            feedback.append('black'); temp_secret[i] = None; temp_guess[i] = None
    for i in range(CODE_LENGTH):
        if temp_guess[i] is not None and temp_guess[i] != 'empty' and temp_guess[i] in temp_secret:
            feedback.append('white'); temp_secret.remove(temp_guess[i])
    random.shuffle(feedback); GAME.guesses.append({"guesser": guesser_name, "guess": guess, "feedback": feedback})
    if data.get('is_final'):
        is_winner = feedback.count('black') == NUM_COLOR_PEGS and len(feedback) == NUM_COLOR_PEGS
        if is_winner:
            emit('game_over', {'winner': guesser_name, 'secret_code': GAME.secret_code}, broadcast=True); GAME.reset_board(); return
        else:
            GAME.players[sid]['eliminated'] = True; emit('eliminated', {'name': guesser_name}, broadcast=True)
    try:
        current_idx = GAME.player_order.index(sid)
        for i in range(1, len(GAME.player_order) + 1):
            next_sid_candidate = GAME.player_order[(current_idx + i) % len(GAME.player_order)]
            if not GAME.players[next_sid_candidate].get("eliminated"):
                GAME.current_turn_sid = next_sid_candidate
                emit('new_turn', {'last_guess': GAME.guesses[-1], 'next_turn': GAME.players[GAME.current_turn_sid]["name"]}, broadcast=True); return
        emit('game_over', {'winner': None, 'message': 'All players eliminated!'}, broadcast=True); GAME.reset_board()
    except (ValueError, IndexError): emit('error', {'message': 'Error finding next player.'})
