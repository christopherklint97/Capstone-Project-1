import requests
from models import db, connect_db, User, Task, Group, Group_Task
import os

from flask import Flask, render_template, request, flash, redirect, session, cli, url_for, abort, g
from flask_cors import CORS
from flask_debugtoolbar import DebugToolbarExtension

from slack_sdk.oauth import AuthorizeUrlGenerator
from slack_sdk.web import WebClient
from slack_sdk.oauth.state_store import FileOAuthStateStore


app = Flask(__name__)

# Activate CORS for flask app
CORS(app)

# Load .env variables
cli.load_dotenv('.env')

# Issue and consume state parameter value on the server-side.
state_store = FileOAuthStateStore(expiration_seconds=300, base_dir="./data")


# Get DB_URI from environ variable (useful for production/testing) or,
# if not set there, use development local db.
app.config['SQLALCHEMY_DATABASE_URI'] = (
    os.environ.get('DATABASE_URL', 'postgres:///dolt'))

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ECHO'] = False
app.config['DEBUG_TB_INTERCEPT_REDIRECTS'] = True
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', "secret123")
toolbar = DebugToolbarExtension(app)

connect_db(app)
db.create_all()

###############################################################################
# Before the requests


@app.before_request
def add_user_to_g():
    """If we're logged in, add curr user to Flask global."""

    if 'CURR_USER_KEY' in session:
        g.user = User.query.get(session['CURR_USER_KEY'])
    else:
        g.user = None


def do_login(user):
    """Log in user."""

    session['CURR_USER_KEY'] = user.id


def do_logout():
    """Logout user."""

    if 'CURR_USER_KEY' in session:
        del session['CURR_USER_KEY']
        del session['token']


######################################################################################
# Home, logging in, logging out

@app.route("/")
def homepage():
    """Show homepage."""

    if g.user:
        # handle the homepage view for a logged in user
        return render_template("home.html", user=g.user)
    else:
        return render_template('login.html')


@app.route("/login")
def login():
    """Login."""

    # Generate a random value and store it on the server-side
    state = state_store.issue()

    # Build https://slack.com/oauth/v2/authorize with sufficient query parameters
    authorize_url_generator = AuthorizeUrlGenerator(
        client_id=os.environ.get("SLACK_CLIENT_ID", None),
        user_scopes=["identity.basic", "identity.email",
                     "identity.team", "identity.avatar"],
        redirect_uri=os.environ.get(
            'SLACK_REDIRECT_URI', 'https%3A%2F%2F127.0.0.1:5000%2Flogin%2Fcallback')
    )

    redirect_uri = authorize_url_generator.generate(state)

    return redirect(redirect_uri)


@app.route("/login/callback")
def login_callback():
    """Handle callback for the login."""

    # Retrieve the auth code from the request params
    if "code" in request.args:
        # Verify the state parameter
        if state_store.consume(request.args["state"]):
            client = WebClient()  # no prepared token needed for this
            # Complete the installation by calling oauth.v2.access API method
            oauth_response = client.oauth_v2_access(
                client_id=os.environ.get("SLACK_CLIENT_ID", None),
                client_secret=os.environ.get("SLACK_CLIENT_SECRET", None),

                code=request.args["code"]
            )

            # Check if the request to Slack API was successful
            if oauth_response['ok'] == True:
                # Saving access token for the authenticated user in the session
                token = oauth_response['authed_user']['access_token']
                session['token'] = token

                # Requesting the Slack identity of the user
                client = WebClient(token=token)
                user_response = client.api_call(
                    api_method='users.identity',
                )

                print(user_response)

                # Check if the request to Slack API was successful
                if user_response['ok'] == True:
                    # Search in db for matching user with Slack ID
                    slack_user_id = user_response['user']['id']
                    user = User.query.filter_by(
                        slack_user_id=slack_user_id).first() or None

                    # If user is found, login
                    if user:
                        do_login(user)
                        flash(f"Hello, {user.name}!", "success")

                    else:
                        # Add the information from Slack into the db
                        name = user_response['user']['name']
                        email = user_response['user']['email']
                        slack_team_id = user_response['team']['id']
                        slack_img_url = user_response['user']['image_512']

                        # Add user to the db
                        user = User(name=name, email=email,
                                    slack_user_id=slack_user_id, slack_team_id=slack_team_id, slack_img_url=slack_img_url)
                        db.session.add(user)
                        db.session.commit()

                        # Login new user
                        user = User.query.filter_by(
                            slack_user_id=slack_user_id).first()
                        do_login(user)
                        flash(f"Hello, {user.name}!", "success")

    return redirect(url_for('homepage'))


@app.route('/logout')
def logout():
    """Handle logout of user."""

    do_logout()
    flash(f"You have been logged out.", "success")
    return redirect("/")