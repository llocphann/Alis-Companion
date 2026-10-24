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
    property var clock:()=>Date.now()
    property var policyState:Policy.newState()
    property string reason:"disabled"
    signal reactionRequested(string expression)
    function revoke(): void {settle.stop();Policy.revoke(policyState);reason="disabled"}
    function observe(): void {
        if(!permitted){revoke();return}
        const proposal=Policy.observe(policyState,{focus:focusPhase,power:powerBand,media:playback},eligible,clock())
        reason=proposal.reason
        if(proposal.eligible)reactionRequested(proposal.expression)
    }
    function schedule(): void {if(permitted)settle.restart();else revoke()}
    onPermittedChanged: {revoke();schedule()}
    onConsentKeyChanged: {revoke();schedule()}
    onEligibleChanged: {if(eligible)schedule();else {settle.stop();observe()}}
    onFocusPhaseChanged:schedule()
    onPowerBandChanged:schedule()
    onPlaybackChanged:schedule()
    Timer {id:settle;interval:250;onTriggered:root.observe()}
    Component.onDestruction:revoke()
}
