#!/usr/bin/env python3
# pylint: disable=invalid-name
''' Use ACRCloud to recognize the file '''

import os
import sys
import time

import logging
import logging.config
import logging.handlers

from PySide2.QtCore import QDir  # pylint: disable=no-name-in-module
from PySide2.QtWidgets import QFileDialog  # pylint: disable=no-name-in-module

import acoustid
import musicbrainzngs

import nowplaying.bootstrap
import nowplaying.config
from nowplaying.recognition import RecognitionPlugin
from nowplaying.exceptions import PluginVerifyError
import nowplaying.version


class Plugin(RecognitionPlugin):
    ''' handler for NowPlaying '''
    def __init__(self, config=None, qsettings=None):
        super().__init__(config=config, qsettings=qsettings)
        self.qwidget = None

    def recognize(self, metadata):  #pylint: disable=too-many-statements
        #if not self.config.cparser.value('acoustidmb/enabled', type=bool):
        #   return None

        if 'filename' not in metadata:
            logging.debug('No filename in metadata')
            return None

        def read_label(releasedata):
            if 'label-info-list' not in releasedata:
                return None

            for labelinfo in releasedata['label-info-list']:
                if 'type' not in labelinfo['label']:
                    continue

                if 'name' in labelinfo['label']:
                    return labelinfo['label']['name']

            return None

        def fetch_from_acoustid(apikey, filename):
            results = None
            try:
                (duration,
                 fingerprint) = acoustid.fingerprint_file(filename,
                                                          force_fpcalc=True)
            except Exception as error:  # pylint: disable=broad-except
                logging.error(
                    "fingerprint could not be calculated for %s due to %s",
                    filename, error)
                return None

            try:
                counter = 0
                while counter < 3:
                    results = acoustid.lookup(apikey, fingerprint, duration)
                    if 'error' in results and 'rate limit' in results['error'][
                            'message']:
                        logging.info(
                            'acoustid complaining about rate limiting. Sleeping then rying again.'
                        )
                        time.sleep(1)
                        counter = counter + 1
                    else:
                        break
            except acoustid.NoBackendError:
                results = None
                logging.error("chromaprint library/tool not found")
            except acoustid.WebServiceError as error:
                results = None
                logging.error("web service request failed: %s", error)
            except Exception as error:  # pylint: disable=broad-except
                results = None
                logging.error('Problem getting a response from Acoustid: %s',
                              error)
            if not results:
                return None

            if 'error' in results and 'rate limit' in results['error'][
                    'message']:
                logging.info('Giving up; rate limit exceeded')
                return None

            return acoustid.parse_lookup_result(results)

        def read_acoustid_tuples(results):
            metadata = {}
            for score, rid, title, artist in results:
                if score > .80:
                    if artist:
                        metadata['artist'] = artist
                    if title:
                        metadata['title'] = title
                    metadata['rid'] = rid
                    break

            return metadata

        if not self.config.cparser.value('acoustidmb/enabled', type=bool):
            return None

        fpcalcexe = self.config.cparser.value('acoustidmb/fpcalcexe')
        if not os.environ.get("FPCALC"):
            os.environ.setdefault("FPCALC", fpcalcexe)
            os.environ["FPCALC"] = fpcalcexe
        apikey = self.config.cparser.value('acoustidmb/acoustidapikey')
        emailaddress = self.config.cparser.value('acoustidmb/emailaddress')
        results = fetch_from_acoustid(apikey, metadata['filename'])
        if not results:
            return None

        acoustidmd = read_acoustid_tuples(results)
        if 'rid' not in acoustidmd:
            logging.info('acoustidmb did not find a musicbrainz rid %s.',
                         metadata['filename'])
            return None
        try:
            musicbrainzngs.set_useragent(
                'whats-now-playing',
                nowplaying.version.get_versions()['version'],
                contact=emailaddress)
        except Exception as error:  # pylint: disable=broad-except
            logging.error('Unable to use MusicBrainz: %s', error)
            return None

        mbdata = musicbrainzngs.browse_releases(recording=acoustidmd['rid'],
                                                includes=['labels'])
        del acoustidmd['rid']
        if 'release-list' in mbdata:
            musicdata = mbdata['release-list'][0]
            if 'title' in musicdata:
                acoustidmd['album'] = musicdata['title']
            if 'date' in musicdata:
                acoustidmd['date'] = musicdata['date']
            label = read_label(musicdata)
            if label:
                metadata['label'] = label
            if 'cover-art-archive' in musicdata and 'artwork' in musicdata[
                    'cover-art-archive'] and musicdata['cover-art-archive'][
                        'artwork']:
                try:
                    acoustidmd['coverimageraw'] = musicbrainzngs.get_image(
                        musicdata['id'], 'front')
                except Exception as error:  # pylint: disable=broad-except
                    logging.error('Failed to get cover art: %s', error)

        return acoustidmd

    def providerinfo(self):  # pylint: disable=no-self-use
        ''' return list of what is provided by this recognition system '''
        return [
            'album',
            'artist',
            'coverimageraw',
            'date',
            'label',
            'title',
        ]

    def connect_settingsui(self, qwidget):
        ''' connect m3u button to filename picker'''
        self.qwidget = qwidget
        qwidget.fpcalcexe_button.clicked.connect(self.on_fpcalcexe_button)

    def on_fpcalcexe_button(self):
        ''' filename button clicked action'''
        if self.qwidget.fpcalcexe_lineedit.text():
            startdir = os.path.dirname(self.qwidget.fpcalcexe_lineedit.text())
        else:
            startdir = QDir.homePath()
        dirname = QFileDialog.getOpenFileName(self.qwidget, 'Select fpcalc',
                                              startdir, 'fpcalc fpcalc.exe')
        if dirname and dirname[0]:
            self.qwidget.fpcalcexe_lineedit.setText(dirname[0])

    def load_settingsui(self, qwidget):
        ''' draw the plugin's settings page '''
        if self.config.cparser.value('acoustidmb/enabled', type=bool):
            qwidget.acoustidmb_checkbox.setChecked(True)
        else:
            qwidget.acoustidmb_checkbox.setChecked(False)
        qwidget.apikey_lineedit.setText(
            self.config.cparser.value('acoustidmb/acoustidapikey'))
        qwidget.emailaddress_lineedit.setText(
            self.config.cparser.value('acoustidmb/emailaddress'))
        qwidget.fpcalcexe_lineedit.setText(
            self.config.cparser.value('acoustidmb/fpcalcexe'))

    def verify_settingsui(self, qwidget):
        ''' no verification to do '''
        if qwidget.acoustidmb_checkbox.isChecked(
        ) and not qwidget.apikey_lineedit.txt():
            raise PluginVerifyError(
                'Acoustid enabled, but no API Key provided.')

        if qwidget.acoustidmb_checkbox.isChecked(
        ) and not qwidget.emailaddress_lineedit.txt():
            raise PluginVerifyError(
                'Acoustid enabled, but no email address provided.')

        if qwidget.acoustidmb_checkbox.isChecked(
        ) and not qwidget.fpcalcexe_lineedit.txt():
            raise PluginVerifyError(
                'Acoustid enabled, but no fpcalc binary provided.')

    def save_settingsui(self, qwidget):
        ''' take the settings page and save it '''
        self.config.cparser.setValue('acoustidmb/enabled',
                                     qwidget.acoustidmb_checkbox.isChecked())
        self.config.cparser.setValue('acoustidmb/acoustidapikey',
                                     qwidget.apikey_lineedit.text())
        self.config.cparser.setValue('acoustidmb/emailaddress',
                                     qwidget.emailaddress_lineedit.text())
        self.config.cparser.setValue('acoustidmb/fpcalcexe',
                                     qwidget.fpcalcexe_lineedit.text())

    def defaults(self, qsettings):
        qsettings.setValue('acoustidmb/enabled', False)
        qsettings.setValue('acoustidmb/acoustidapikey', None)
        qsettings.setValue('acoustidmb/emailaddress', None)
        qsettings.setValue('acoustidmb/fpcalcexe', None)


def main():
    ''' integration test '''
    filename = sys.argv[1]

    bundledir = os.path.abspath(os.path.dirname(__file__))
    logging.basicConfig(level=logging.DEBUG)
    nowplaying.bootstrap.set_qt_names()
    # need to make sure config is initialized with something
    nowplaying.config.ConfigFile(bundledir=bundledir)
    plugin = Plugin()
    metadata = plugin.recognize({'filename': filename})
    if not metadata:
        print('No information')
        sys.exit(1)

    if 'coverimageraw' in metadata:
        print('got an image')
        del metadata['coverimageraw']
    print(metadata)


if __name__ == "__main__":
    main()
