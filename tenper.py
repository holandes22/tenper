#!/usr/bin/env python
"""Tenper is a tmux wrapper with support for Python virtualenv.

It uses virtualenvwrapper's conventions, so they'll work side-by-side.

The name is a corruption of gibberish:
    tmuxvirtualenvwrapper -> tenvpapper -> tenper.
"""

import argparse
import os
import re
import shutil
import subprocess

import yaml


shell = os.getenv('SHELL')
editor = os.getenv('EDITOR')
configs = os.path.join(os.path.expanduser('~'), '.tenper')
virtualenvs = os.path.join(os.path.expanduser('~'), '.virtualenvs')
config_template = """# Shows up in 'tmux list-sessions' and on the left side of the status bar.
session name: {env}

# Optional; if provided, the virtualenv activate script will be sourced in all
# new windows and panes by setting tmux's default-command option.
virtualenv:
    python binary: /usr/bin/python
    site packages?: false
    path: $HOME/.venv #optional path to virtualenv

# When starting a tenper session, all windows and panes will be changed to this
# directory.
project root: $HOME

# Environment variables (only available inside the tmux session).
environment:
    MYHOME: $HOME

windows:
  - name: One
    panes:
      - ls -l

  - name: Two
    # Layout of the panes: even-horizontal, even-vertical, main-horizontal,
    # main-vertical, or tiled. You can also specify the layout string in the
    # list-windows command (see the layout section section in tmux's man page).
    layout: main-vertical
    panes:
      - ls
      - vim
      - top
"""


def command_list(template, **kwargs):
    """Split a command into an array (for subprocess).

    >>> command_list('ls')
    ['ls']
    >>> command_list('ls /')
    ['ls', '/']
    >>> command_list('echo {message}', message='Hello, world.')
    ['echo', 'Hello, world.']

    Args:
        template: A string that will be split on ' ' and will have the
            remaining arguments to this function applied to the 'format' of
            each segment.

    Returns:
        A list of strings.
    """

    return [part.format(**kwargs) for part in template.split(' ')]


def run(cmd, **kwargs):
    """Run a command template (see command_list)."""

    return subprocess.call(command_list(cmd, **kwargs))


def config_for(env):
    """Return config (parsed YAML) for an environment.

    Args:
        env: An environment name.
    Returns:
        A dictionary of configuration.

    Raises:
        Exception; config file not found.
    """
    fn = os.path.join(configs, '{}.yml'.format(env))
    if os.path.exists(fn):
        with open(fn, 'r') as f:
            return yaml.load(f)

    raise Exception((
        'No configuration found for environment "{}". '
        '(Checked: {})').format(env, fn))


def confirm_virtualenv(config, delete_first=False):
    """Make sure the virtualenv is set up, if needed.

    Args:
        config: The environment dictionary.
        delete_first: If this is true, we'll delete an existing virtualenv.
    """
    # Short circuit: we're not using a virtualenv.
    if not config.get('virtualenv'):
        return


    custom_path = config['virtualenv'].get('path', None)
    path = os.path.expandvars(custom_path) if custom_path else os.path.join(virtualenvs, config['session name'])

    # Short circuit: virtualenv exists and we're not deleting it.
    if os.path.exists(path) and not delete_first:
        return

    if delete_first:
        shutil.rmtree(path)

    run('virtualenv -p {python_binary} {site_packages} {dir}',
        python_binary=config['virtualenv'].get('python binary', '/usr/bin/python'),
        site_packages='--system-site-packages' if config['virtualenv'].get('site packages?', False) \
            else '--no-site-packages',
        dir=path)


def list_envs(*args):
    """Print a list of the available yaml file names to stdout."""

    if os.path.exists(configs):
        args = [f[0:-4] for f in os.listdir(configs) if f.endswith('.yml')]
        for yml in args:
            print(yml)


def edit(env):
    """Edit an environment; creates a new one if it doesn't exist."""

    if not os.path.exists(configs):
        os.mkdir(configs)

    config_file = os.path.join(configs, '{}.yml'.format(env))
    if not os.path.exists(config_file):
        with open(config_file, 'w') as f:
            f.write(config_template.format(env=env))

    run('{editor} {file}', editor=editor, file=config_file)


def delete(env):
    """Delete an environment; prompted to delete the virtualenv if it
    exists."""

    config_file = os.path.join(configs, '{}.yml'.format(env))
    virtualenv = os.path.join(virtualenvs, env)

    if os.path.exists(virtualenv):
        prompt = (
            'There\'s a virtualenv for this environment in {}. Do you want to '
            'delete it? ').format(virtualenv)
        try:
            resp = raw_input(prompt)
        except (ValueError, EOFError):
            resp = input(prompt)

        if resp.strip() in ['yes', 'YES', 'y', 'Y']:
            shutil.rmtree(virtualenv)
            print('Deleted {}.'.format(virtualenv))

    os.remove(config_file)
    print('Removed {}.'.format(config_file))

    try:
        # Clean up after ourselves.
        os.rmdir(configs)
    except OSError:
        pass


def rebuild(env):
    """Rebuild a virtualenv."""

    confirm_virtualenv(config_for(env), delete_first=True)


def start(env):
    config = config_for(env)
    confirm_virtualenv(config)
    session = config['session name']
    virtualenv = config.get('virtualenv', None)
    virtualenv_path = None if not virtualenv else \
        os.path.expandvars(
            os.path.join(config['virtualenv'].get('path', None) or os.path.join(virtualenvs, session),
                         'bin',
                         'activate')
        )

    # Short circuit for a preexisting session.
    if run('tmux has-session -t {session}', session=config['session name']) == 0:
        prompt = 'Session already exists: attaching. (Press any key to continue.)'
        try:
            raw_input(prompt)
        except (ValueError, EOFError):
            input(prompt)

        run('tmux -2 attach-session -t {session}', session=config['session name'])
        return

    # Start the session.
    run('tmux new-session -d -s {session}', session=session)

    # Set session default path
    run('tmux set-option -t {session} default-path {path}',
        session=session,
        path=os.path.expandvars(config['project root']))

    # Resize the left status area so that the full name of the environment will
    # fit.
    run('tmux set-option -t {session} status-left-length {length}',
        session=config['session name'],
        # There's brackets surrounding the name, thus: + 2.
        length=len(config['session name'])+2)

    # Add project specific environment variables.
    if config.get('environment'):
        for k, v in config['environment'].iteritems():
            # Evaluate environment vars embedded in the config.
            v = re.sub(
                r'\$([a-zA-Z][a-zA-Z0-9_]*)',
                lambda match: os.environ.get(match.group(1), ''),
                v)

            run('tmux set-environment -t {session} {key} {value}',
                session=session,
                key=k,
                value=v)

    # Base index
    # ----------
    # The first window won't have the environment variables set with
    # set-environment. We need to create a new window, remove the initial
    # window and move the new one to the old place. There's a base-index option
    # that isn't showing up for me in the show-options command so I will use
    # list-windows and pull the index off the first line.
    base_index = int(
        subprocess.check_output(
            'tmux list-windows -t {session}'.format(session=session),
            shell=True).split(':')[0])

    for index, window in enumerate(config['windows']):
        window_target = ':'.join([session, str(base_index + index)])

        # If this is the first window, we need to kill the initial window and
        # move this one to base_index.
        if index == 0:
            base_target = ':'.join([session, str(base_index)])
            temp_target = ':'.join([session, str(base_index + 1)])

            run('tmux new-window -d -t {temp_target} -n {name}',
                temp_target=temp_target,
                name=window['name'])

            run('tmux kill-window -t {base_target}',
                base_target=base_target)

            run('tmux move-window -s {temp_target} -t {base_target}',
                temp_target=temp_target,
                base_target=base_target)
        else:
            # Create the window (or rename the first window).
            run('tmux new-window -d -t {window_target} -n {name}',
                window_target=window_target,
                name=window['name'])

        # Panes can also have a base index so we need to check it with the same
        # method used on windows.
        base_pindex = int(
            subprocess.check_output(
                'tmux list-panes -t {window_target}'.format(window_target=window_target),
                shell=True).split(':')[0])

        for pindex, pane in enumerate(window.get('panes', [])):
            pane_target = '{}.{}'.format(window_target, str(base_pindex + pindex))

            if pindex != 0:
                run('tmux split-window -t {window_target}.0',
                    window_target=window_target)

            # Activate the virtualenv.
            if virtualenv:
                run('tmux send-keys -t {pane_target} {command} ENTER',
                    pane_target=pane_target,
                    command='source {}'.format(virtualenv_path))

            # Run the window command, if available.
            if pane:
                run('tmux send-keys -t {pane_target} {command} ENTER',
                    pane_target=pane_target,
                    command=pane)

        if window.get('layout'):
            run('tmux select-layout -t {window_target} {layout}',
                window_target=window_target,
                layout=window['layout'])

        run('tmux select-pane -t {window_target}.{base_pindex}',
            window_target=window_target,
            base_pindex=base_pindex)

    run('tmux -2 attach-session -t {session}',
        session=session)


def parse_args(args):
    """Return a function and its arguments based on 'args'.

    Args:
        args: Probably never anything but sys.argv[1:].

    Returns:
        (callable, [...arguments])
    """

    parser = argparse.ArgumentParser(description=(
        'A wrapper for tmux sessions and (optionally) virtualenv{,wrapper}. '
        'Usage:\n'
        '    tenper list\n'
        '    tenper edit my-project\n'
        '    tenper rebuild my-project\n'
        '    tenper delete my-project\n'
        '    tenper my-project\n'))

    if len(args) == 1:
        # Either 'list' or a project name.
        parser.add_argument('project_name')

    else:
        # Subcommand.
        subparsers = parser.add_subparsers(dest='command')

        def mksubparser(name, help_text):
            sp = subparsers.add_parser(name, help=help_text)
            sp.add_argument('project_name')

        mksubparser('edit', 'Edit a project\'s configuration.')
        mksubparser('rebuild', 'Delete an existing virtualenv and start a new one.')
        mksubparser('delete', 'Delete a project\'s virtualenv and configuration.')

    parsed = parser.parse_args(args)

    handler = None
    project = parsed.project_name if parsed.project_name else None

    if parsed.project_name == 'list':
        handler = list_envs

    elif parsed.project_name == 'completions':
        handler = completions

    elif hasattr(parsed, 'command'):
        handler = globals()[parsed.command]

    else:
        handler = start

    return (handler, project)


def completions(*args):
    """Generate available completions.

    This is used in tenper-completion.sh and can be used in a .zshrc to provide
    tab completion by adding the following line.

        compdef "_arguments '*: :($(tenper completions))'" tenper

    Returns:
        A string of the available commands and current environments, e.g.:
            edit list delete rebuild foo bar baz
    """
    args = ['list', 'edit', 'rebuild', 'delete']

    if os.path.exists(configs):
        for f in os.listdir(configs):
            if f.endswith('.yml'):
                args.append(f[0:-4])

    print(' '.join(args))
