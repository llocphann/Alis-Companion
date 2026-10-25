import QtQuick
import "../modules/abyss/companion/WullAmbientPolicy.js" as Policy

// One event-settle deadline, never a Focus clock or polling/inference loop.
Item {
    id:root
    property bool permitted:false
    property bool eligible:false
    property string consentKey:""
    property string focusPhase:"unavailable"
    property string powerBand:"unavailable"
    property string playback:"unavailable"
    property string idleState:"unavailable"
    property string resultCategory:"unavailable"
    property string activeActivity:"none"
    property string activeProp:""
    property string lastGesture:""
    property bool pendingObserve:false
    property var clock:()=>Date.now()
    property var policyState:Policy.newState()
    property string reason:"disabled"
    signal reactionRequested(string expression)
    signal performanceRequested(string expression, string gesture, string prop, int ttlMs)
    function clearPerformance(): void {
        activeActivity="none"
        activeProp=""
        lastGesture=""
    }
    function revoke(): void {
        pendingObserve=false
        settle.stop()
        clearPerformance()
        Policy.revoke(policyState)
        reason="disabled"
    }
    function observe(): void {
        pendingObserve=false
        if(!permitted){revoke();return}
        if(!eligible || (focusPhase!=="focus" && activeProp==="book") || (focusPhase!=="break" && activeProp==="coffee"))
            clearPerformance()
        const proposal=Policy.observe(policyState,{
            focus:focusPhase,
            power:powerBand,
            media:playback,
            idle:idleState,
            result:resultCategory
        },eligible,clock())
        reason=proposal.reason
        if(proposal.eligible) {
            const perf=Policy.performance(proposal.reason,focusPhase)
            activeActivity=perf.activity
            activeProp=perf.prop
            lastGesture=perf.gesture
            reactionRequested(proposal.expression)
            performanceRequested(proposal.expression,perf.gesture,perf.prop,perf.ttlMs)
            if(perf.prop!=="" && perf.ttlMs>0 && !pendingObserve) {
                settle.interval=perf.ttlMs
                settle.restart()
            }
        } else if(eligible && activeProp==="") {
            if(focusPhase==="focus") {
                activeActivity="reading"
                activeProp="book"
            } else if(focusPhase==="break") {
                activeActivity="coffee"
                activeProp="coffee"
            }
        }
    }
    function schedule(): void {
        if(permitted) {
            pendingObserve=true
            settle.interval=250
            settle.restart()
        } else revoke()
    }
    onPermittedChanged: {revoke();schedule()}
    onConsentKeyChanged: {revoke();schedule()}
    onEligibleChanged: {
        if(eligible)schedule()
        else {pendingObserve=false;settle.stop();clearPerformance();observe()}
    }
    onFocusPhaseChanged: {
        if((focusPhase!=="focus" && activeProp==="book") || (focusPhase!=="break" && activeProp==="coffee"))
            clearPerformance()
        schedule()
    }
    onPowerBandChanged:schedule()
    onPlaybackChanged:schedule()
    onIdleStateChanged:schedule()
    onResultCategoryChanged:schedule()
    Timer {
        id:settle
        interval:250
        onTriggered: {
            if(root.pendingObserve)root.observe()
            else if((root.focusPhase==="focus" && root.activeProp==="book")
                    || (root.focusPhase==="break" && root.activeProp==="coffee"))
                root.lastGesture=""
            else root.clearPerformance()
        }
    }
    Component.onDestruction:revoke()
}
