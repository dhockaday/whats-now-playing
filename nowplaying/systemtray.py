#!/usr/bin/env python3
''' system tray '''

import logging

from PySide6.QtWidgets import QApplication, QErrorMessage, QMenu, QMessageBox, QSystemTrayIcon  # pylint: disable=no-name-in-module
from PySide6.QtGui import QAction, QActionGroup, QIcon  # pylint: disable=no-name-in-module
from PySide6.QtCore import QFileSystemWatcher  # pylint: disable=no-name-in-module

import nowplaying.config
import nowplaying.db
import nowplaying.settingsui
import nowplaying.subprocesses
import nowplaying.utils
import nowplaying.version

LASTANNOUNCED = {'artist': None, 'title': None}


class Tray:  # pylint: disable=too-many-instance-attributes
    ''' System Tray object '''

    def __init__(self):  #pylint: disable=too-many-statements
        self.config = nowplaying.config.ConfigFile()
        self.version = nowplaying.version.get_versions()['version']

        self.icon = QIcon(str(self.config.iconfile))
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(self.icon)
        self.tray.setToolTip("Now Playing ▶")
        self.tray.setVisible(True)
        self.menu = QMenu()

        # create systemtray options and actions
        self.action_title = QAction(f'What\'s Now Playing v{self.version}')
        self.menu.addAction(self.action_title)
        self.action_title.setEnabled(False)

        self.subprocesses = nowplaying.subprocesses.SubprocessManager(
            self.config)
        self.settingswindow = nowplaying.settingsui.SettingsUI(
            tray=self, version=self.version)

        self.action_config = QAction("Settings")
        self.action_config.triggered.connect(self.settingswindow.show)
        self.menu.addAction(self.action_config)
        self.menu.addSeparator()

        self.action_newestmode = QAction('Newest')
        self.action_oldestmode = QAction('Oldest')
        self.mixmode_actiongroup = QActionGroup(self.tray)
        self._configure_newold_menu()
        self.menu.addSeparator()

        self.action_pause = QAction()
        self._configure_pause_menu()
        self.menu.addSeparator()

        self.action_exit = QAction("Exit")
        self.action_exit.triggered.connect(self.cleanquit)
        self.menu.addAction(self.action_exit)

        # add menu to the systemtray UI
        self.tray.setContextMenu(self.menu)
        self.tray.show()

        self.error_dialog = QErrorMessage()
        self.regular_dialog = QMessageBox()

        self.config.get()
        if not self.config.cparser.value('settings/input', defaultValue=None):
            self.installer()
        else:
            self.action_pause.setText('Pause')
            self.action_pause.setEnabled(True)

        self.fix_mixmode_menu()

        self._check_for_upgrade_alert()

        self.subprocesses.start_all_processes()

        # Start the track notify handler
        metadb = nowplaying.db.MetadataDB()
        self.watcher = QFileSystemWatcher()
        self.watcher.addPath(str(metadb.databasefile))
        self.watcher.fileChanged.connect(self.tracknotify)

    def _configure_newold_menu(self):
        self.action_newestmode.setCheckable(True)
        self.action_newestmode.setEnabled(True)
        self.action_oldestmode.setCheckable(True)
        self.action_oldestmode.setEnabled(False)
        self.menu.addAction(self.action_newestmode)
        self.menu.addAction(self.action_oldestmode)
        self.mixmode_actiongroup.addAction(self.action_newestmode)
        self.mixmode_actiongroup.addAction(self.action_oldestmode)
        self.action_newestmode.triggered.connect(self.newestmixmode)
        self.action_oldestmode.triggered.connect(self.oldestmixmode)

    def _configure_pause_menu(self):
        self.action_pause.triggered.connect(self.pause)
        self.menu.addAction(self.action_pause)
        self.action_pause.setEnabled(False)

    def _check_for_upgrade_alert(self):
        nowplaying.settingsui.update_twitchbot_commands(self.config)
        if self.config.cparser.value('settings/newtemplates', type=bool):
            self.regular_dialog.setText('Updated templates have been placed.')
            self.config.cparser.setValue('settings/newtemplates', False)
            self.regular_dialog.show()
            self.regular_dialog.exec()

        if self.config.cparser.value('settings/newtwitchbot', type=bool):
            self.regular_dialog.setText(
                'Twitchbot permissions have been added or changed.')
            self.config.cparser.setValue('settings/newtwitchbot', False)
            self.regular_dialog.show()
            self.regular_dialog.exec()

    def webenable(self, status):
        ''' If the web server gets in trouble, we need to tell the user '''
        if not status:
            self.settingswindow.disable_web()
            self.settingswindow.show()
            self.pause()

    def obswsenable(self, status):
        ''' If the OBS WebSocket gets in trouble, we need to tell the user '''
        if not status:
            self.settingswindow.disable_obsws()
            self.settingswindow.show()
            self.pause()

    def unpause(self):
        ''' unpause polling '''
        self.config.unpause()
        self.action_pause.setText('Pause')
        self.action_pause.triggered.connect(self.pause)

    def pause(self):
        ''' pause polling '''
        self.config.pause()
        self.action_pause.setText('Resume')
        self.action_pause.triggered.connect(self.unpause)

    def fix_mixmode_menu(self):
        ''' update the mixmode based upon current rules '''
        plugins = self.config.cparser.value('settings/input',
                                            defaultValue=None)
        if not plugins:
            return

        validmixmodes = self.config.validmixmodes()

        if 'oldest' in validmixmodes:
            self.action_oldestmode.setEnabled(True)
        else:
            self.action_oldestmode.setEnabled(False)

        if 'newest' in validmixmodes:
            self.action_newestmode.setEnabled(True)
        else:
            self.action_newestmode.setEnabled(False)

        if self.config.getmixmode() == 'newest':
            self.action_newestmode.setChecked(True)
            self.action_oldestmode.setChecked(False)
        else:
            self.action_oldestmode.setChecked(True)
            self.action_newestmode.setChecked(False)

    def oldestmixmode(self):
        ''' enable active mixing '''
        self.config.setmixmode('oldest')
        self.fix_mixmode_menu()

    def newestmixmode(self):
        ''' enable passive mixing '''
        self.config.setmixmode('newest')
        self.fix_mixmode_menu()

    def tracknotify(self):  # pylint: disable=unused-argument
        ''' signal handler to update the tooltip '''
        global LASTANNOUNCED  # pylint: disable=global-statement, global-variable-not-assigned

        self.config.get()
        if self.config.notif:
            metadb = nowplaying.db.MetadataDB()
            metadata = metadb.read_last_meta()
            if not metadata:
                return

            # don't announce empty content
            if not metadata['artist'] and not metadata['title']:
                logging.warning(
                    'Both artist and title are empty; skipping notify')
                return

            if 'artist' in metadata:
                artist = metadata['artist']
            else:
                artist = ''

            if 'title' in metadata:
                title = metadata['title']
            else:
                title = ''

            if metadata['artist'] == LASTANNOUNCED['artist'] and \
               metadata['title'] == LASTANNOUNCED['title']:
                return

            LASTANNOUNCED['artist'] = metadata['artist']
            LASTANNOUNCED['title'] = metadata['title']

            tip = f'{artist} - {title}'
            self.tray.setIcon(self.icon)
            self.tray.showMessage('Now Playing ▶ ',
                                  tip,
                                  icon=QSystemTrayIcon.NoIcon)
            self.tray.show()

    def cleanquit(self):
        ''' quit app and cleanup '''

        logging.debug('Starting shutdown')
        self.tray.setVisible(False)

        self.subprocesses.stop_all_processes()

        if self.config:
            self.config.get()
            if self.config.file:
                logging.debug('Writing empty file')
                nowplaying.utils.writetxttrack(filename=self.config.file,
                                               clear=True)
        app = QApplication.instance()
        app.exit(0)

    def installer(self):
        ''' make some guesses as to what the user needs '''
        if self.config.cparser.value('input/settings', defaultValue=None):
            return

        self.regular_dialog.setText(
            'New installation! Thanks! '
            'Determining setup. This operation may take a bit!')
        self.regular_dialog.show()
        self.regular_dialog.exec()

        plugins = self.config.pluginobjs['inputs']

        for plugin in plugins:
            if plugins[plugin].install():
                break

        self.regular_dialog.setText(
            'Basic configuration hopefully in place. '
            'Please verify the Source and configure an output!')
        self.regular_dialog.show()
        self.regular_dialog.exec()

        self.settingswindow.show()
