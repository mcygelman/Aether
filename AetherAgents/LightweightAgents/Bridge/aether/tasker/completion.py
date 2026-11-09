"""Verify applied staff closure receipts; never infer completion from prose or file a reflection."""
import hashlib
import sqlite3
from . import staff
from ._files import read_json

def staff_complete(sprint):
    for agent in sprint.record().get('staff',[]):
        try:
            receipt=read_json(staff.closure_folder(sprint,agent)/staff.CLOSED_RECORD,{})
            if receipt.get('applied') is not True:return False
            result=receipt.get('result_sha256')
            # A later sprint's write-back backs this result up as 'home-before'; completion stays a fact about this
            # sprint as it closed, not about the home today (11-04, Opus by Michel's word while Praxis was away).
            current=hashlib.sha256(staff.home_file(sprint.data_root,agent).read_bytes()).hexdigest()
            if result!=current and not (result and staff.was_home(sprint.data_root,agent,result)):return False
        except (OSError,ValueError,AttributeError,sqlite3.Error):return False
    return True
