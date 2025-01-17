#!/usr/bin/env python3
''' Input Plugin definition '''

import logging

import nowplaying.config
from nowplaying.exceptions import PluginVerifyError


class ArtistExtrasPlugin():
    ''' base class of plugins '''

    def __init__(self, config=None, qsettings=None):
        self.plugintype = 'input'
        if config:
            self.config = config

        if qsettings:
            self.defaults(qsettings)
            return

        if not config:  # pragma: no cover
            self.config = nowplaying.config.ConfigFile()

#### Settings UI methods

    def defaults(self, qsettings):
        ''' (re-)set the default configuration values for this plugin '''
        raise NotImplementedError

    def connect_settingsui(self, qwidget):
        ''' connect any UI elements such as buttons '''
        raise NotImplementedError

    def load_settingsui(self, qwidget):
        ''' load values from config and populate page '''
        raise NotImplementedError

    def verify_settingsui(self, qwidget):  #pylint: disable=no-self-use
        ''' verify the values in the UI prior to saving '''
        raise PluginVerifyError('Plugin did not implement verification.')

    def save_settingsui(self, qwidget):
        ''' take the settings page and save it '''
        raise NotImplementedError


#### Plug-in methods

    def download(self, metadata=None, imagecache=None):  #pylint: disable=no-self-use
        ''' return metadata '''
        raise NotImplementedError

    def providerinfo(self):
        ''' return list of what is provided by this recognition system '''
        raise NotImplementedError
