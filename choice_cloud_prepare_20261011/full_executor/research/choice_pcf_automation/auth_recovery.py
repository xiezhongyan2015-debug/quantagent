"""Bounded reauthentication and mandatory reconciliation; never retries a write."""
import datetime as dt
import json
import uuid
from choice_auth import LoginUnavailable,HumanLoginRequired
from quanta_agents.factor_lab_a.choice_pcf import TradeError,SessionExpired,FillSnapshotPending
from watch_engine import save


class RecoveryStopped(TradeError):pass


class RecoveryController:
    DELAYS=(300,600,900)
    def __init__(self,watcher,transport,login,install,clock,sleeper,status_callback=None,native_login=None,initial_deadline=None):
        self.w=watcher;self.transport=transport;self.login=login;self.install=install
        self.clock=clock;self.sleep=sleeper;self.status_callback=status_callback or (lambda phase:None)
        self.native_login=native_login
        self.initial_deadline=initial_deadline or clock().replace(hour=9,minute=50,second=0,microsecond=0)
        self.info=self.w.state.setdefault('auth_recovery',dict(attempts=0,phase='ready',events=[]))

    def update(self,phase,**fields):
        self.info.update(phase=phase,updated_at=self.clock().isoformat(),**fields)
        self.w.flush();self.status_callback(phase)

    def permitted(self):
        if self.clock().date().isoformat()!=self.w.state['day'] or self.clock().time()>=dt.time(15):raise RecoveryStopped('Recovery date/window expired; no writes permitted')
        if self.w.path.with_name('STOP').exists():raise RecoveryStopped('STOP requested during recovery')

    def wait(self,seconds,initial=False):
        until=self.clock()+dt.timedelta(seconds=seconds)
        self.update('waiting_relogin',next_attempt_at=until.isoformat())
        while self.clock()<until:
            self.permitted()
            if initial and self.clock()>self.initial_deadline:raise RecoveryStopped('Initial login missed launch window')
            self.sleep(min(30,max(0,(until-self.clock()).total_seconds())))
        self.permitted()
        if initial and self.clock()>self.initial_deadline:raise RecoveryStopped('Initial login missed launch window')

    def verify(self):
        deadline=self.clock()+dt.timedelta(seconds=90);consistent=0
        while consistent<2:
            self.permitted()
            try:self.w.audit();consistent+=1
            except FillSnapshotPending:consistent=0
            if self.clock()>=deadline:raise TradeError('Post-login account snapshots did not converge')
            if consistent<2:self.sleep(5)
        # Pending orders are allowed only if their exact IDs and quantities are
        # known. An unresolved earlier cancellation is never sent a second time.
        if any(a.get('cancel_requested_at') and not a.get('terminal') for a in self.w.state['attempts']):
            raise TradeError('Earlier cancellation unresolved after login; manual review required')

    def recover(self,initial=False):
        self.transport.writes_suspended=True
        self.update('authentication_lost')
        try:
            counter='initial_attempts' if initial else 'attempts'
            self.info.setdefault(counter,0)
            while self.info[counter]<len(self.DELAYS):
                n=self.info[counter];delay=(0,300,600)[n] if initial else self.DELAYS[n]
                self.wait(delay,initial=initial)
                self.info[counter]+=1
                event=dict(at=self.clock().isoformat(),attempt=self.info[counter],initial=initial)
                self.info['events'].append(event);self.update('logging_in')
                try:
                    try:tokens=self.login()
                    except HumanLoginRequired:
                        if self.native_login is None:raise
                        self.update('waiting_native_login')
                        self.native_login(self,initial)
                        tokens=self.login()
                    self.install(tokens);tokens.clear()
                    self.update('reconciling_after_login');self.verify()
                except HumanLoginRequired:
                    event['result']='human_verification_required';self.update('human_verification_required');raise
                except (LoginUnavailable,SessionExpired) as e:
                    event['result']=type(e).__name__;self.update('login_attempt_failed');continue
                event['result']='verified';self.w.last_submit=self.clock()
                self.update('authenticated_and_reconciled')
                self.transport.writes_suspended=False
                return
            raise RecoveryStopped('Daily automatic login attempt limit reached')
        except Exception:
            # This flag stays set even if a newly acquired token is valid. The
            # runner may inspect the account but cannot clean up or resume writes
            # without a successful reconciliation gate.
            self.transport.writes_suspended=True
            if self.info['phase']!='human_verification_required':self.update('recovery_stopped_review_required')
            raise


def native_login_bridge(controller,initial=False):
    """Request normal Choice UI login from the scheduled in-thread UI agent.

    The trading process never automates arbitrary windows, submits credentials
    to an unverified page, or accepts a reply as proof of account correctness.
    A successful UI reply is followed by official SSO and two full reconciliations.
    """
    parent=controller.w.path.parent
    now=controller.clock();deadline=min(now+dt.timedelta(minutes=12),now.replace(hour=15,minute=0,second=0,microsecond=0))
    if initial:deadline=min(deadline,controller.initial_deadline)
    request=dict(request_id=uuid.uuid4().hex,day=controller.w.state['day'],issued_at=now.isoformat(),deadline=deadline.isoformat(),
                 status='pending',account_mode='existing_competition_simulation_only',
                 credential_source='Windows current-user DPAPI vault; never log plaintext',
                 action='Normal Choice native login, then open Choice financial lab to update its cookies; no trading')
    save(parent/'native_login_request.json',request)
    try:
        while controller.clock()<deadline:
            controller.permitted()
            reply=parent/'native_login_reply.json'
            if reply.exists():
                data=json.loads(reply.read_text(encoding='utf-8-sig'))
                if data.get('request_id')==request['request_id']:
                    if data.get('status')=='completed':return
                    if data.get('status') in ('human_verification_required','failed'):raise HumanLoginRequired('Normal Choice login needs user attention')
            controller.update('waiting_native_login',native_request_id=request['request_id'],native_deadline=deadline.isoformat())
            controller.sleep(min(30,max(0,(deadline-controller.clock()).total_seconds())))
        raise HumanLoginRequired('Normal Choice login did not complete before its deadline')
    finally:
        request['status']='closed';request['closed_at']=controller.clock().isoformat();save(parent/'native_login_request.json',request)
