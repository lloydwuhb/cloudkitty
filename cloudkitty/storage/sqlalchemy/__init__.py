# -*- coding: utf-8 -*-
# Copyright 2014 Objectif Libre
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# @author: Stéphane Albert
#
import json

from oslo.db.sqlalchemy import utils
import sqlalchemy

from cloudkitty import db
from cloudkitty import storage
from cloudkitty.storage.sqlalchemy import migration
from cloudkitty.storage.sqlalchemy import models
from cloudkitty import utils as ck_utils


class SQLAlchemyStorage(storage.BaseStorage):
    """SQLAlchemy Storage Backend

    """
    def __init__(self, period=3600):
        super(SQLAlchemyStorage, self).__init__(period)
        self._session = None

    @staticmethod
    def init():
        migration.upgrade('head')

    def _commit(self):
        self._session.commit()
        self._session.begin()

    def _dispatch(self, data):
        for service in data:
            for frame in data[service]:
                self._append_time_frame(service, frame)
            # HACK(adriant) Quick hack to allow billing windows to
            # progress. This check/insert probably ought to be moved
            # somewhere else.
            if not data[service]:
                empty_frame = {'vol': {'qty': 0, 'unit': 'None'},
                               'billing': {'price': 0}, 'desc': ''}
                self._append_time_frame(service, empty_frame)

    def append(self, raw_data):
        if not self._session:
            self._session = db.get_session()
            self._session.begin()
        super(SQLAlchemyStorage, self).append(raw_data)

    def get_state(self):
        session = db.get_session()
        r = utils.model_query(
            models.RatedDataFrame,
            session
        ).order_by(
            models.RatedDataFrame.begin.desc()
        ).first()
        if r:
            return ck_utils.dt2ts(r.begin)

    def get_total(self):
        model = models.RatedDataFrame

        # Boundary calculation
        month_start = ck_utils.get_month_start()
        month_end = ck_utils.get_next_month()

        session = db.get_session()
        rate = session.query(
            sqlalchemy.func.sum(model.rate).label('rate')
        ).filter(
            model.begin >= month_start,
            model.end <= month_end
        ).scalar()
        return rate

    def get_time_frame(self, begin, end, **filters):
        """Return a list of time frames.

        :param start: Filter from `start`.
        :param end: Filter to `end`.
        :param unit: Filter on an unit type.
        :param res_type: Filter on a resource type.
        """
        model = models.RatedDataFrame
        session = db.get_session()
        q = utils.model_query(
            model,
            session
        ).filter(
            model.begin >= ck_utils.ts2dt(begin),
            model.end <= ck_utils.ts2dt(end)
        )
        for cur_filter in filters:
            q = q.filter(getattr(model, cur_filter) == filters[cur_filter])
        r = q.all()
        if not r:
            raise storage.NoTimeFrame()
        return [entry.to_cloudkitty() for entry in r]

    def _append_time_frame(self, res_type, frame):
        vol_dict = frame['vol']
        qty = vol_dict['qty']
        unit = vol_dict['unit']
        rating_dict = frame['billing']
        rate = rating_dict['price']
        desc = json.dumps(frame['desc'])
        self.add_time_frame(self.usage_start_dt,
                            self.usage_end_dt,
                            unit,
                            qty,
                            res_type,
                            rate,
                            desc)

    def add_time_frame(self, begin, end, unit, qty, res_type, rate, desc):
        """Create a new time frame.

        """
        frame = models.RatedDataFrame(begin=begin,
                                      end=end,
                                      unit=unit,
                                      qty=qty,
                                      res_type=res_type,
                                      rate=rate,
                                      desc=desc)
        self._session.add(frame)
