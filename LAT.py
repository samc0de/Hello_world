"""
The script basically generates the request_data_object and posts to BT.
"""
import copy
import getpass
import json
import logging
import os
import sys

from sqlalchemy.exc import DBAPIError

from etl.bbg_transport.dto import RequestDataItem, RequestItem, RequestOptionItem
from etl.core import util
from etl.core.util import uri_post, sanitize_cmd_line
from etl.repo.fnd_cfdw.etl_config import EtlConfigRepo
from etl.repo.pim_pm.pl_bbg_batch import PlBbgBatchRepo
from etl.repo.pim_pm.pl_bbg_batch_series_vw import PlBbgBatchSeriesVwRepo

USAGE = ['Queuer Agent',
         # ('--source_code',
         #  {
         #      'help': 'Source code',
         #      'default': 'PL_BT_POLL_AGENT'
         #  }
         #  ),

         ]

class QueuerAgent(object):
    """

    """

    def __init__(self, logger=None, options=None):

        logging.info('QueuerAgent')
        self.USERNAME = getpass.getuser()
        md = EtlConfigRepo.instance.get_by_config_code('PL_BT_ENDPOINT')
        self.base_url = md.config_value
        md = EtlConfigRepo.instance.get_by_config_code('PL_BT_DESCRIPTION')
        self.description = md.config_value
        md = EtlConfigRepo.instance.get_by_config_code('PL_BT_FORMAT')
        self.response_format = md.config_value
        md = EtlConfigRepo.instance.get_by_config_code('PL_BT_REQUESTOR_CODE')
        self.requestor_code = md.config_value

    def __enter__(self):
        # make a database connection and return it
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):

        if exc_type is None:
            # No exception
            pass


    @staticmethod
    def get_request(repo):
        """

        :param repo:
        :return:
        """
        model = repo.model
        try:
            data = repo.query.filter(model.batch_status_code == 'IN_QUEUE').all()
            return data
        except DBAPIError as err:
            logging.error(err)

    def get_priority_list(self, result):
        """

        :param result:
        :return:
        """
        data_list = list()
        history_list = list()
        for i in result:
            if i.bbg_program_code == 'GETDATA':
                data_list.append(i)
            else:
                history_list.append(i)
        data_list = self.get_priority_list_by_interface_code(data_list)
        history_list = self.get_priority_list_by_interface_code(history_list)
        plist = data_list+history_list
        return plist

    @staticmethod
    def get_priority_list_by_interface_code(result):
        """

        :param result:
        :return:
        """
        plist = []
        for i in result:
            if i.bbg_interface_code == 'SAPI':
                plist.insert(0, i)
            else:
                plist.append(i)
        return plist

    def get_request_object(self, objdata, result_series):
        """

        :param objdata:
        :param result_series:
        :return:
        """
        data_items = [RequestDataItem(tag=i.pl_series_code, yellow_key=i.bbg_yellow,
                                      ticker=i.bbg_ticker) for i in result_series]
        headers = self.get_headers(objdata)
        fields = self.get_request_fields(result_series)
        request_options = [RequestOptionItem(option_name=key, option_value=headers[key])
                           for key in headers]

        request = RequestItem(request_description=self.description,
                              requestor_code=self.requestor_code,
                              program_code=objdata.bbg_program_code,
                              interface_code=objdata.bbg_interface_code,
                              response_format_code=self.response_format,
                              request_data_items=data_items,
                              request_options=request_options,
                              request_fields=fields)

        payload = json.dumps(request.to_json())
        return payload

    @staticmethod
    def get_headers(objdata):
        """

        :param objdata:
        :return:
        """
        headers = dict()
        if objdata.bbg_program_code == "GETDATA":
            headers['DATERANGE'] = str(objdata.asof_end_date_key) + \
                                   "|" + str(objdata.asof_end_date_key)

        elif objdata.bbg_program_code == "GETHISTORY":
            headers['DATERANGE'] = str(objdata.asof_start_date_key) + \
                                   "|" + str(objdata.asof_end_date_key)
        return headers

    @staticmethod
    def get_request_fields(result_series):
        """

        :param result_series:
        :return:
        """
        request_fields_list = []

        for i in result_series:
            request_fields_list.append(i.bbg_mnemonic)

        return list(set(request_fields_list))

    def post_to_bt(self, payload):
        """

        :param payload:
        :return:
        """
        response = uri_post(self.base_url + 'request_data', payload)
        return response

    @staticmethod
    def update_request(batch_id, bt_request_id, bt_status_code,
                       request_obj, batch_status_code, repo):

        """

        :param batch_id:
        :param bt_request_id:
        :param bt_status_code:
        :param request_obj:
        :param batch_status_code:
        :param repo:
        :return:
        """
        model = repo.model
        try:
            update_row = repo.query.filter(model.batch_status_code == 'IN_QUEUE',
                                           model.batch_id == batch_id).all()
            update_row[0].batch_status_code = batch_status_code
            update_row[0].bt_request_id = bt_request_id
            update_row[0].bt_status_code = bt_status_code
            update_row[0].bt_request_payload = request_obj
            repo.save(update_row)
        except DBAPIError as err:
            logging.error(err)

    def run(self):
        """

        :return:
        """
        result = self.get_request(PlBbgBatchRepo())
        priority_list = self.get_priority_list(result)
        for i in priority_list:
            repo = PlBbgBatchSeriesVwRepo()
            model = repo.model
            try:
                result_batch = repo.query.filter(model.batch_id == i.batch_id).all()
                obj = self.get_request_object(i, result_batch)
                response = self.post_to_bt(obj)
                self.update_request(i.batch_id, response['request_id'],
                                    str(response['request_status']),
                                    str(obj), 'SENT_TO_BT', PlBbgBatchRepo())
            except Exception as err:
                logging.error(err)


def main():
    """
    Delegates all processing to Agent instance.
    """
    logger = logging.getLogger("{}".format(
        os.path.splitext(os.path.basename(__file__))[0]))

    try:
        cmd_line = sanitize_cmd_line(copy.copy(sys.argv))
        logging.info(cmd_line)
        args = util.parse_args(*USAGE)
        logging.info("Agent started")
        with QueuerAgent(logger=logger, options=args) as agent:
            agent.run()

    except Exception as ex:
        logger.critical("Agent exited with error: %s", ex)
        return -1
    else:
        logger.info("Agent completed successfully.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
