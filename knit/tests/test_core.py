import os
import time
import pytest
import socket

from knit import Knit
from knit.exceptions import YARNException, KnitException


def check_docker():
    """check if inside docker container"""
    return os.path.exists('/.dockerenv')

inside_docker = check_docker


def wait_for_status(k, status, timeout=30):
    cur_status = k.runtime_status()
    while cur_status != status and timeout > 0:
        print("Current status is {0}, waiting for {1}".format(cur_status, status))

        time.sleep(2)
        timeout -= 2
        cur_status = k.runtime_status()

    time.sleep(1)
    return timeout > 0
        

def wait_for_containers(k, running_containers, timeout=30):
    cur_running_containers = k.status()['runningContainers']
    while cur_running_containers != running_containers and timeout > 0:
        print("Current number of containers is {0}, waiting for {1}".format(cur_running_containers, running_containers))

        time.sleep(2)
        timeout -= 2
        cur_running_containers = k.status()['runningContainers']

    time.sleep(1)
    return timeout > 0


@pytest.yield_fixture
def k():
    knitter = Knit(nn='localhost', rm='localhost', nn_port=8020, rm_port=8088,
                   replication_factor=1)
    try:
        yield knitter
    finally:
        # always kill, to avoid follow-on resource pressure
        try:
            knitter.kill()
        except:
            pass


def test_argument_parsing(k):
    cmd = "sleep 10"
    with pytest.raises(KnitException):
        k.start(cmd, files='a,b,c', memory=128)

    with pytest.raises(KnitException):
        k.start(cmd, memory='128')


def test_cmd(k):
    cmd = "hostname"
    k.start(cmd, memory=128)
    logs0 = k.logs()

    if not k.wait_for_completion(30):
        k.kill()

    time.sleep(5)
    hostname = socket.gethostname()
    logs1 = k.logs(shell=True)

    assert hostname in str(logs0)
    assert hostname in str(logs1)


def test_allocation_errors(k):
    with pytest.raises(KnitException):
        k.start('env', 1, memory=1000000000)
    with pytest.raises(KnitException):
        k.start('env', 1, virtual_cores=1100000)
    with pytest.raises(KnitException):
        k.start('env', 10000, virtual_cores=1, memory=1)


def test_multiple_containers(k):
    cmd = "sleep 30"
    k.start(cmd, num_containers=2, memory=128)

    wait_for_status(k, 'RUNNING')

    got_containers = wait_for_containers(k, 3)

    # wait for job to finish
    if not k.wait_for_completion(30):
        k.kill()

    if not got_containers:
        logs = k.logs(shell=True)
        print(logs)

    assert got_containers


def test_add_remove_containers(k):
    cmd = "sleep 600"
    k.start(cmd, num_containers=1, memory=128)

    wait_for_status(k, 'RUNNING')

    assert wait_for_containers(k, 2)

    containers = k.get_containers()
    assert len(containers) == 2

    k.add_containers(num_containers=1)

    assert wait_for_containers(k, 3)

    containers = list(k.get_container_statuses())
    assert len(containers) == 3
    k.remove_containers(containers[1])

    assert wait_for_containers(k, 2)
    containers = k.get_containers()
    assert len(containers) == 2

    with pytest.raises(KnitException):
        k.remove_containers('blah')


def test_memory(k):
    cmd = "sleep 10"
    k.start(cmd, num_containers=2, memory=300)

    wait_for_status(k, 'RUNNING')

    time.sleep(2)
    status = k.status()

    # not exactly sure on getting an exact number
    # 300*2+128(AM)
    assert status['allocatedMB'] >= 728, status['allocatedMB']

    # wait for job to finish
    if not k.wait_for_completion(30):
        k.kill()


def test_cmd_w_conda_env(k):
    env_zip = k.create_env(env_name='dev', packages=['python=2.7'], remove=True)
    cmd = "$PYTHON_BIN -c 'import sys; print(sys.version_info);" \
          " import random; print(str(random.random()))'"
    k.start(cmd, env=env_zip, memory=128)

    if not k.wait_for_completion(30):
        k.kill()

    time.sleep(5)  # log aggregation
    logs = k.logs(shell=True)
    assert "'final'" in str(logs)  # part of version string


def test_hdfs_home():
    hdfs3 = pytest.importorskip('hdfs3')
    hdfs = hdfs3.HDFileSystem()
    d = '/tmp/test'
    try:
        hdfs.mkdir(d)
        k = Knit(nn='localhost', rm='localhost', nn_port=8020, rm_port=8088,
               replication_factor=1, hdfs_home=d)

        env_zip = k.create_env(env_name='dev', packages=['python=2.7'], remove=True)
        k.start('env', env=env_zip, memory=128)

        assert d + '/.knitDeps' in hdfs.ls(d, False)
        assert d + "/.knitDeps/knit-1.0-SNAPSHOT.jar" in hdfs.ls(d + '/.knitDeps', False)
        assert d + "/.knitDeps/dev.zip" in hdfs.ls(d + '/.knitDeps', False)
        if not k.wait_for_completion(30):
            k.kill()

        time.sleep(5)  # log aggregation
        logs = k.logs()
        assert "PYTHON_BIN=" in str(logs)
        assert "CONDA_PREFIX=" in str(logs)
    finally:
        hdfs.rm(d, True)
        k.kill()


cur_dir = os.path.dirname(__file__)
txt_file = os.path.join(cur_dir, 'files', 'upload_file.txt')
py_file = os.path.join(cur_dir, 'files', 'read_uploaded.py')


def test_file_uploading(k):
    cmd = 'python ./read_uploaded.py'
    k.start(cmd, files=[txt_file, py_file])

    if not k.wait_for_completion(30):
        k.kill()

    time.sleep(5)
    logs = k.logs()
    assert "rambling on" in str(logs), str(logs)


def test_kill_status(k):
    cmd = "sleep 10"
    k.start(cmd, num_containers=1)

    wait_for_status(k, 'RUNNING')

    assert k.kill()

    time.sleep(1)
    status = k.runtime_status()
    assert status in ['KILLED', 'NONE']


def test_yarn_kill_status(k):
    cmd = "sleep 10"
    app_id = k.start(cmd, num_containers=1)

    wait_for_status(k, 'RUNNING')

    assert k.yarn_api.kill(app_id)

    time.sleep(1)
    status = k.runtime_status()
    assert status in ['KILLED', 'NONE']


def test_lang(k):
    cmd = "env"
    k.start(cmd, num_containers=1, lang='en_US.utf-8')

    wait_for_status(k, 'FINISHED')
    time.sleep(2)
    out = k.logs()
    assert "LANG=en_US.utf-8" in str(out)


def test_logs(k):
    cmd = "sleep 10"
    k.start(cmd, num_containers=1)

    wait_for_status(k, 'RUNNING')

    assert k.logs()

    if not k.wait_for_completion(30):
        k.kill()

    time.sleep(5)
    assert k.logs()


def test_kill_all(k):
    # this is really a yarn api test
    cmd = "sleep 100"
    k.start(cmd, num_containers=1)
    # kill all knit apps
    k.yarn_api.kill_all()
    time.sleep(2)
    assert k.runtime_status() == 'KILLED'
