try:
    from scipy.stats import poisson, expon
except ImportError:
    poisson = expon = None
    print "Unable to import from scipy.  ScenarioPoisson and ScenarioExpon are disabled"

try:
    from psutil import Process, cpu_percent
except ImportError:
    Process = cpu_percent = None
    print "Unable to import from psutil.  Process statistics are disabled"

from hashlib import sha1
from os import getpid, uname
from random import random, uniform
from re import compile as re_compile
from sys import maxsize
from time import time

from ..crypto import ec_generate_key, ec_to_public_bin, ec_to_private_bin
from ..dprint import dprint
from ..member import Member
from ..script import ScriptBase
from .ldecoder import Parser, NextFile

class ScenarioScript(ScriptBase):
    def __init__(self, *args, **kargs):
        super(ScenarioScript, self).__init__(*args, **kargs)
        self._my_member = None
        self._master_member = None
        self._cid = sha1(self.master_member_public_key).digest()
        self._is_joined = False

        self.log("scenario-init", peernumber=int(self._kargs["peernumber"]), hostname=uname()[1])

        if self.enable_statistics and Process and cpu_percent:
            self._process = Process(getpid()) if self.enable_statistics or self.enable_statistics else None
            self._dispersy.callback.register(self._periodically_log_statistics)

    @property
    def enable_wait_for_wan_address(self):
        return False

    @property
    def enable_statistics(self):
        return 30.0

    def run(self):
        self.add_testcase(self._run_scenario)

    def _run_scenario(self):
        for deadline, _, call, args in self.parse_scenario():
            yield max(0.0, deadline - time())
            if __debug__: dprint(call.__name__)
            if call(*args) == "END":
                return

    @property
    def my_member_security(self):
        return u"low"

    @property
    def master_member_public_key(self):
        raise NotImplementedError("must return an experiment specific master member public key")
            # if False:
            #     # when crypto.py is disabled a public key is slightly
            #     # different...
            #     master_public_key = ";".join(("60", master_public_key[:60].encode("HEX"), ""))
        # return "3081a7301006072a8648ce3d020106052b81040027038192000404668ed626c6d6bf4a280cf4824c8cd31fe4c7c46767afb127129abfccdf8be3c38d4b1cb8792f66ccb603bfed395e908786049cb64bacab198ef07d49358da490fbc41f43ade33e05c9991a1bb7ef122cda5359d908514b3c935fe17a3679b6626161ca8d8d934d372dec23cc30ff576bfcd9c292f188af4142594ccc5f6376e2986e1521dc874819f7bcb7ae3ce400".decode("HEX")

    @property
    def community_class(self):
        raise NotImplementedError("must return an experiment community class")

    @property
    def community_args(self):
        return ()

    @property
    def community_kargs(self):
        return {}

    def log(self, _message, **kargs):
        pass

    def _periodically_log_statistics(self):
        while True:
            # CPU
            self.log("scenario-cpu", percentage=cpu_percent(interval=0, percpu=True))

            # memory
            rss, vms = self._process.get_memory_info()
            self.log("scenario-memory", rss=rss, vms=vms)

            # bandwidth
            self.log("scenario-bandwidth",
                     up=self._dispersy.endpoint.total_up, down=self._dispersy.endpoint.total_down,
                     drop=self._dispersy.statistics.drop_count, success=self._dispersy.statistics.success_count)

            # wait
            yield self.enable_statistics

    def parse_scenario(self):
        """
        Returns a list with (TIMESTAMP, FUNC, ARGS) tuples, where TIMESTAMP is the time when FUNC
        must be called.

        [@+][H:]M:S[-[H:]M:S] METHOD [ARG1 [ARG2 ..]] [{PEERNR1 [, PEERNR2, ...] [, PEERNR3-PEERNR6, ...]}]
        ^^^^
        use @ to schedule events based on experiment startstamp
        use + to schedule events based on peer startstamp
            ^^^^^^^^^^^^^^^^^
            schedule event hours:minutes:seconds after @ or +
            or add another hours:minutes:seconds pair to schedule uniformly chosen between the two
                              ^^^^^^^^^^^^^^^^^^^^^^^
                              calls script.schedule_METHOD(ARG1, ARG2)
                              the arguments are passed as strings
                                                      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                                      apply event only to peer 1 and 2, and peers in
                                                      range 3-6 (including both 3 and 6)
        """
        scenario = []
        re_line = re_compile("".join(("^",
                                      "(?P<origin>[@+])",
                                      "\s*",
                                      "(?:(?P<beginH>\d+):)?(?P<beginM>\d+):(?P<beginS>\d+)",
                                      "(?:\s*-\s*",
                                      "(?:(?P<endH>\d+):)?(?P<endM>\d+):(?P<endS>\d+)",
                                      ")?",
                                      "\s+",
                                      "(?P<method>\w+)(?P<args>\s+(.+?))??",
                                      "(?:\s*{(?P<peers>\s*!?\d+(?:-\d+)?(?:\s*,\s*!?\d+(?:-\d+)?)*\s*)})?",
                                      "\s*(?:\n)?$")))
        peernumber = int(self._kargs["peernumber"])
        filename = self._kargs["scenario"]
        origin = {"@":float(self._kargs["startstamp"]) if "startstamp" in self._kargs else time(),
                  "+":time()}

        for lineno, line in enumerate(open(filename, "r")):
            match = re_line.match(line)
            if match:
                # remove all entries that are None (allows us to get default per key)
                dic = dict((key, value) for key, value in match.groupdict().iteritems() if not value is None)

                # get the peers, if any, for which this line applies
                yes_peers = set()
                no_peers = set()
                for peer in dic.get("peers", "").split(","):
                    peer = peer.strip()
                    if peer:
                        # if the peer number (or peer number pair) is preceded by '!' it negates the result
                        if peer.startswith("!"):
                            peer = peer[1:]
                            peers = no_peers
                        else:
                            peers = yes_peers
                        # parse the peer number (or peer number pair)
                        if "-" in peer:
                            low, high = peer.split("-")
                            peers.update(xrange(int(low), int(high)+1))
                        else:
                            peers.add(int(peer))

                if not (yes_peers or no_peers) or (yes_peers and peernumber in yes_peers) or (no_peers and not peernumber in no_peers):
                    begin = int(dic.get("beginH", 0)) * 3600.0 + int(dic.get("beginM", 0)) * 60.0 + int(dic.get("beginS", 0))
                    end = int(dic.get("endH", 0)) * 3600.0 + int(dic.get("endM", 0)) * 60.0 + int(dic.get("endS", 0))
                    assert end == 0.0 or begin <= end, "when end time is given it must be at or after the start time"
                    scenario.append((origin[dic.get("origin", "@")] + begin + (random() * (end - begin) if end else 0.0),
                                     lineno,
                                     getattr(self, "scenario_" + dic.get("method", "print")),
                                     tuple(dic.get("args", "").split())))

        assert scenario, "scenario is empty"
        assert any(func.__name__ == "scenario_end" for _, _, func, _ in scenario), "scenario end is not defined"
        assert any(func.__name__ == "scenario_start" for _, _, func, _ in scenario), "scenario start is not defined"
        scenario.sort()

        for deadline, _, func, args in scenario:
            if __debug__: dprint("scenario: @", int(deadline - origin["@"]), "s ", func.__name__)
            self.log("scenario-schedule", deadline=int(deadline - origin["@"]), func=func.__name__, args=args)

        return scenario

    def has_community(self, load=False, auto_load=False):
        try:
            return self._dispersy.get_community(self._cid, load=load, auto_load=auto_load)
        except KeyError:
            return None

    def scenario_start(self):
        assert self.has_community() == None
        ec = ec_generate_key(self.my_member_security)
        self._my_member = Member(ec_to_public_bin(ec), ec_to_private_bin(ec))
        self._master_member = Member(self.master_member_public_key)
        self.log("scenario-start", my_member=self._my_member.mid, master_member=self._master_member.mid, classification=self.community_class.get_classification())

    def scenario_end(self):
        if __debug__: dprint("END")
        self.log("scenario-end")
        return "END"

    def scenario_print(self, *args):
        dprint(*args, glue=" ", force=True)

    def scenario_churn(self, state, duration=None):
        assert isinstance(state, str), type(state)
        assert state in ("online", "offline"), state
        assert duration is None or isinstance(duration, (str, float)), type(duration)

        duration = None if duration == None else float(duration)
        community = self.has_community()

        if state == "online":
            if community is None:
                if __debug__: dprint("online for the next ", duration, " seconds")
                self.log("scenario-churn", state="online", duration=duration)

                if self._is_joined:
                    self.community_class.load_community(self._master_member, *self.community_args, **self.community_kargs)

                else:
                    if __debug__: dprint("join community ", self._master_member.mid.encode("HEX"), " as ", self._my_member.mid.encode("HEX"))
                    community = self.community_class.join_community(self._master_member, self._my_member, *self.community_args, **self.community_kargs)
                    community.auto_load = False
                    self._is_joined = True

            else:
                if __debug__: dprint("online for the next ", duration, " seconds (we are already online)")
                self.log("scenario-churn", state="stay-online", duration=duration)

        elif state == "offline":
            if community is None:
                if __debug__: dprint("offline (we are already offline)")
                self.log("scenario-churn", state="stay-offline")

            else:
                if __debug__: dprint("offline")
                self.log("scenario-churn", state="offline")
                community.unload_community()

        else:
            raise ValueError("state must be either 'online' or 'offline'")

if poisson:
    class ScenarioPoisson(object):
        def __init__(self, *args, **kargs):
            self.__poisson_online_mu = 0.0
            self.__poisson_offline_mu = 0.0

        def __poisson_churn(self):
            while True:
                delay = float(poisson.rvs(self.__poisson_online_mu))
                self.scenario_churn("online", delay)
                yield delay

                delay = float(poisson.rvs(self.__poisson_offline_mu))
                self.scenario_churn("offline", delay)
                yield delay

        def scenario_poisson_churn(self, online_mu, offline_mu):
            self.__poisson_online_mu = float(online_mu)
            self.__poisson_offline_mu = float(offline_mu)
            self.log("scenario-poisson-churn", online_mu=self.__poisson_online_mu, offline_mu=self.__poisson_offline_mu)
            self._dispersy.callback.persistent_register("scenario-poisson-identifier", self.__poisson_churn)

if expon:
    class ScenarioExpon(object):
        def __init__(self, *args, **kargs):
            self.__expon_online_beta = 0.0
            self.__expon_offline_beta = 0.0
            self.__expon_online_threshold = 0.0
            self.__expon_min_online = 0.0
            self.__expon_max_online = 0.0
            self.__expon_offline_threshold = 0.0
            self.__expon_max_offline = 0.0
            self.__expon_min_offline = 0.0

        def __expon_churn(self):
            while True:
                delay = expon.rvs(scale=self.__expon_online_beta)
                if delay >= self.__expon_online_threshold:
                    delay = float(min(self.__expon_max_online, max(self.__expon_min_online, delay)))
                    self.scenario_churn("online", delay)
                    yield delay

                delay = expon.rvs(scale=self.__expon_offline_beta)
                if delay >= self.__expon_offline_threshold:
                    delay = float(min(self.__expon_max_offline, max(self.__expon_min_offline, delay)))
                    self.scenario_churn("offline", delay)
                    yield delay

        def scenario_expon_churn(self, online_beta, offline_beta, online_threshold="DEF", min_online="DEF", max_online="DEF", offline_threshold="DEF", min_offline="DEF", max_offline="DEF"):
            self.__expon_online_beta = float(online_beta)
            self.__expon_offline_beta = float(offline_beta)
            self.__expon_online_threshold = float("5.0" if online_threshold == "DEF" else online_threshold)
            self.__expon_min_online = float("5.0" if min_online == "DEF" else min_online)
            self.__expon_max_online = float(maxsize if max_online == "DEF" else max_online)
            self.__expon_offline_threshold = float("5.0" if offline_threshold == "DEF" else offline_threshold)
            self.__expon_min_offline = float("5.0" if min_offline == "DEF" else min_offline)
            self.__expon_max_offline = float(maxsize if max_offline == "DEF" else max_offline)
            self.log("scenario-expon-churn", online_beta=self.__expon_online_beta, offline_beta=self.__expon_offline_beta, online_threshold=self.__expon_online_threshold, min_online=self.__expon_min_online, max_online=self.__expon_max_online, offline_threshold=self.__expon_offline_threshold, min_offline=self.__expon_min_offline, max_offline=self.__expon_max_offline)
            self._dispersy.callback.persistent_register("scenario-expon-identifier", self.__expon_churn)

class ScenarioUniform(object):
    def __init__(self, *args, **kargs):
        self.__uniform_online_low = 0.0
        self.__uniform_online_high = 0.0
        self.__uniform_offline_low = 0.0
        self.__uniform_offline_high = 0.0

    def __uniform_churn(self):
        while True:
            delay = float(uniform(self.__uniform_online_low, self.__uniform_online_high))
            self.scenario_churn("online", delay)
            yield delay

            delay = float(uniform(self.__uniform_offline_low, self.__uniform_offline_high))
            self.scenario_churn("offline", delay)
            yield float(delay)

    def scenario_uniform_churn(self, online_mean, online_mod="DEF", offline_mean="DEF", offline_mod="DEF"):
        online_mean = float(online_mean)
        online_mod = float("0.50" if online_mod == "DEF" else online_mod)
        offline_mean = float("120.0" if offline_mean == "DEF" else offline_mean)
        offline_mod = float("0.0" if offline_mod == "DEF" else offline_mod)
        self.__uniform_online_low = online_mean * (1.0 - online_mod)
        self.__uniform_online_high = online_mean * (1.0 + online_mod)
        self.__uniform_offline_low = offline_mean * (1.0 - offline_mod)
        self.__uniform_offline_high = offline_mean * (1.0 + offline_mod)
        self.log("scenario-uniform-churn", online_low=self.__uniform_online_low, online_high=self.__uniform_online_high, offline_low=self.__uniform_offline_low, offline_high=self.__uniform_offline_high)
        self._dispersy.callback.persistent_register("scenario-uniform-identifier", self.__uniform_churn)

class ScenarioParser1(Parser):
    def __init__(self, database):
        super(ScenarioParser1, self).__init__()

        self.db = database
        self.cur = database.cursor()
        self.cur.execute(u"CREATE TABLE peer (id INTEGER PRIMARY KEY, hostname TEXT, mid BLOB)")

        self.peer_id = 0

        self.mapto(self.scenario_init, "scenario-init")
        self.mapto(self.scenario_start, "scenario-start")

    def scenario_init(self, timestamp, name, peernumber, hostname):
        self.peer_id = peernumber
        self.cur.execute(u"INSERT INTO peer (id, hostname) VALUES (?, ?)", (peernumber, hostname))

    def scenario_start(self, timestamp, name, my_member, master_member, classification):
        self.cur.execute(u"UPDATE peer SET mid = ? WHERE id = ?", (buffer(my_member), self.peer_id))
        raise NextFile()

    def parse_directory(self, *args, **kargs):
        try:
            super(ScenarioParser1, self).parse_directory(*args, **kargs)
        finally:
            self.db.commit()

class ScenarioParser2(Parser):
    def __init__(self, database):
        super(ScenarioParser2, self).__init__()

        self.db = database
        self.cur = database.cursor()
        self.cur.execute(u"CREATE TABLE cpu (timestamp FLOAT, peer INTEGER, percentage FLOAT)")
        self.cur.execute(u"CREATE TABLE memory (timestamp FLOAT, peer INTEGER, rss INTEGER, vms INTEGER)")
        self.cur.execute(u"CREATE TABLE bandwidth (timestamp FLOAT, peer INTEGER, up INTEGER, down INTEGER, loss INTEGER, success INTEGER, up_rate INTEGER, down_rate INTEGER)")
        self.cur.execute(u"CREATE TABLE churn (peer INTEGER, online FLOAT, offline FLOAT)")

        self.mid_cache = {}
        self.hostname = ""
        self.mid = ""
        self.peer_id = 0

        self.online_timestamp = 0.0
        self.bandwidth_timestamp = 0
        self.bandwidth_up = 0
        self.bandwidth_down = 0

        self.io_timestamp = 0.0
        self.io_read_bytes = 0
        self.io_read_count = 0
        self.io_write_bytes = 0
        self.io_write_count = 0

        self.mapto(self.scenario_init, "scenario-init")
        self.mapto(self.scenario_start, "scenario-start")
        self.mapto(self.scenario_end, "scenario-end")
        self.mapto(self.scenario_churn, "scenario-churn")
        self.mapto(self.scenario_cpu, "scenario-cpu")
        self.mapto(self.scenario_memory, "scenario-memory")
        self.mapto(self.scenario_bandwidth, "scenario-bandwidth")

    def start_parser(self, filename):
        """Called once before starting to parse FILENAME"""
        super(ScenarioParser2, self).start_parser(filename)

        self.online_timestamp = 0.0
        self.bandwidth_timestamp = 0
        self.bandwidth_up = 0
        self.bandwidth_down = 0

    def get_peer_id_from_mid(self, mid):
        try:
            return self.mid_cache[mid]
        except KeyError:
            try:
                peer_id, = self.cur.execute(u"SELECT id FROM peer WHERE mid = ?", (buffer(mid),)).next()
            except StopIteration:
                raise ValueError(mid)
            else:
                if peer_id is None:
                    raise ValueError(mid)
                else:
                    self.mid_cache[mid] = peer_id
                    return peer_id

    def scenario_init(self, timestamp, _, peernumber, hostname):
        self.hostname = hostname
        self.peer_id = peernumber
        self.bandwidth_timestamp = timestamp

    def scenario_start(self, timestamp, _, my_member, master_member, classification):
        self.mid = my_member

    def scenario_end(self, timestamp, _):
        if self.online_timestamp:
            self.cur.execute(u"INSERT INTO churn (peer, online, offline) VALUES (?, ?, ?)", (self.peer_id, self.online_timestamp, timestamp))

    def scenario_churn(self, timestamp, _, state, **kargs):
        if state == "online":
            self.online_timestamp = timestamp

        elif state == "offline":
            assert self.online_timestamp
            self.cur.execute(u"INSERT INTO churn (peer, online, offline) VALUES (?, ?, ?)", (self.peer_id, self.online_timestamp, timestamp))
            self.online_timestamp = 0.0

    def scenario_cpu(self, timestamp, _, percentage):
        self.cur.execute(u"INSERT INTO cpu (timestamp, peer, percentage) VALUES (?, ?, ?)", (timestamp, self.peer_id, sum(percentage) / len(percentage)))

    def scenario_memory(self, timestamp, _, vms, rss):
        self.cur.execute(u"INSERT INTO memory (timestamp, peer, rss, vms) VALUES (?, ?, ?, ?)", (timestamp, self.peer_id, rss, vms))

    def scenario_bandwidth(self, timestamp, _, down, up, drop, success):
        delta = timestamp - self.bandwidth_timestamp
        self.cur.execute(u"INSERT INTO bandwidth (timestamp, peer, up, down, loss, success, up_rate, down_rate) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (timestamp, self.peer_id, up, down, drop, success, (up-self.bandwidth_up)/delta, (down-self.bandwidth_down)/delta))
        self.bandwidth_timestamp = timestamp
        self.bandwidth_up = up
        self.bandwidth_down = down

    def parse_directory(self, *args, **kargs):
        try:
            super(ScenarioParser2, self).parse_directory(*args, **kargs)
        finally:
            self.db.commit()
