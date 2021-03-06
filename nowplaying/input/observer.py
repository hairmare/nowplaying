#! /usr/bin/env python
# -*- coding: utf-8 -*-

__version__ = "$Revision$"
# $Id$

import logging
import logging.handlers
import os
import time
import xml.dom.minidom

import isodate
import pytz

from nowplaying import show, track

logger = logging.getLogger("now-playing")

SHOW_NAME_KLANGBECKEN = "Klangbecken"
SHOW_URL_KLANGBECKEN = "http://www.rabe.ch/sendungen/musik/klangbecken.html"


class InputObserver:
    def __init__(self, current_show_url):
        # http://intranet.rabe.ch/mantisbt/view.php?id=20
        self.current_show_url = current_show_url

        self.first_run = True
        self.previous_saemubox_id = None
        self.show = None
        self.showclient = show.client.ShowClient(current_show_url)
        self.show = self.showclient.get_show_info()

        self.previous_show_uuid = None

        self.track_handler = None

    def add_track_handler(self, track_handler):
        self.track_handler = track_handler

    def handle_id(self, saemubox_id):
        return False

    def update(self, saemubox_id):
        if self.handle_id(saemubox_id):
            self.handle()

    def handle(self):
        pass


class KlangbeckenInputObserver(InputObserver):
    def __init__(self, current_show_url, input_file):
        InputObserver.__init__(self, current_show_url)

        self.input_file = input_file
        self.last_modify_time = os.stat(self.input_file).st_mtime

        self.track = None

    def handle_id(self, saemubox_id):
        # only handle Klangbecken output
        if saemubox_id == 1:
            return True

        return False

    def handle(self):
        # @TODO: replace the stat method with inotify
        modify_time = os.stat(self.input_file).st_mtime

        # @TODO: Need to check if we have a stale file and send default
        #        track infos in this case. This might happend if loopy
        #        went out for lunch...
        #        pseudo code: now > modify_time + self.track.get_duration()

        if self.first_run or modify_time > self.last_modify_time:
            logger.info("Now playing file changed")

            self.show = self.showclient.get_show_info()
            self.last_modify_time = modify_time

            logger.info("First run: %s" % self.first_run)

            if not self.first_run:
                logger.info("calling track_finished")
                self.track_handler.track_finished(self.track)

            self.track = self.get_track_info()

            # Klangbecken acts as a failover and last resort input, if other
            # active inputs are silent or have problems.
            # Therefore the show's name should always be Klangbecken, regardless
            # of what loopy thinks.
            if self.show.name != SHOW_NAME_KLANGBECKEN:
                logger.info(
                    "Klangbecken Input active, overriding current show '%s' with '%s'"
                    % (self.show.name, SHOW_NAME_KLANGBECKEN)
                )

                self.show = show.show.Show()
                self.show.set_name(SHOW_NAME_KLANGBECKEN)
                self.show.set_url(SHOW_URL_KLANGBECKEN)

                # Set the show's end time to the one of the track, as we have
                # no idea for how long the Klangbecken input will be active.
                # The show's start time is initially set to now.
                self.show.set_endtime(self.track.endtime)

            self.track.set_show(self.show)

            self.track_handler.track_started(self.track)

            self.first_run = False

    def get_track_info(self):
        dom = xml.dom.minidom.parse(self.input_file)

        # default track info
        track_info = {
            "artist": track.track.DEFAULT_ARTIST,
            "title": track.track.DEFAULT_TITLE,
            "album": "",
            "track": "",
            "time": "",
        }

        song = dom.getElementsByTagName("song")

        if len(song) == 0 or song[0].hasChildNodes() is False:
            raise Exception("No <song> tag found")

        song = song[0]

        for name in list(track_info.keys()):
            elements = song.getElementsByTagName(name)

            if len(elements) == 0:
                raise Exception("No <%s> tag found" % name)
            elif elements[0].hasChildNodes():
                element_data = elements[0].firstChild.data.strip()

                if element_data != "":
                    track_info[name] = element_data
                else:
                    logger.info("Element %s has empty value, ignoring" % name)

        if not song.hasAttribute("timestamp"):
            raise Exception("Song timestamp attribute is missing")

        # set the start time and append the missing UTC offset
        # @TODO: The UTC offset should be provided by the now playing XML
        #        generated by Thomas
        # ex.: 2012-05-15T09:47:07+02:00
        track_info["start_timestamp"] = song.getAttribute("timestamp") + time.strftime(
            "%z"
        )

        current_track = track.track.Track()

        current_track.set_artist(track_info["artist"])
        current_track.set_title(track_info["title"])
        current_track.set_album(track_info["album"])

        # Store as UTC datetime object
        current_track.set_starttime(
            isodate.parse_datetime(track_info["start_timestamp"]).astimezone(
                pytz.timezone("UTC")
            )
        )

        current_track.set_duration(track_info["time"])

        return current_track


class NonKlangbeckenInputObserver(InputObserver):
    """Observer for input that doesn't originate from klangbecken and therefore misses the track information.

    Uses the show's name instead of the actual track infos
    """

    def handle_id(self, saemubox_id):

        if saemubox_id != self.previous_saemubox_id:
            # If sämubox changes, force a show update, this acts as
            # a self-healing measurement in case the show web service provides
            # nonsense ;)
            self.show = self.showclient.get_show_info(True)

        self.previous_saemubox_id = saemubox_id

        # only handle non-Klangbecken
        if saemubox_id != 1:
            return True

        return False

    def handle(self):
        self.show = self.showclient.get_show_info()

        # only handle if a new show has started
        if self.show.uuid != self.previous_show_uuid:
            logger.info("Show changed")
            self.track_handler.track_started(self.get_track_info())
            self.previous_show_uuid = self.show.uuid

    def get_track_info(self):
        current_track = track.track.Track()

        current_track.set_artist(track.track.DEFAULT_ARTIST)
        current_track.set_title(track.track.DEFAULT_TITLE)

        # Set the track's start/end time to the start/end time of the show
        current_track.set_starttime(self.show.starttime)
        current_track.set_endtime(self.show.endtime)

        current_track.set_show(self.show)

        return current_track
