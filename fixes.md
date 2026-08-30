Here is the **final combined prompt** you can directly give to Claude:

> ## VisionX — Windows-Specific Gesture & Voice Control Fix
>
> I am developing and running **VisionX specifically on Windows with Microsoft PowerPoint**. I want you to inspect the existing project and fix the following issues at their root cause. **Do not disable, remove, or bypass the personalized gesture model or trained voice model as a workaround. Preserve the existing functionality.**
>
> ### 1. Gesture recognition is unstable / commands repeat
>
> During a live PowerPoint presentation, when I show or hold gestures such as **Pinky, Index, etc.**, the web app keeps changing the displayed action or slide number continuously/randomly.
>
> A gesture should **not execute repeatedly on every camera frame**. For example, holding a Next Slide gesture should trigger the action once, not continuously move through multiple slides.
>
> Inspect the complete pipeline:
>
> ```text
> Camera
> ↓
> MediaPipe landmarks
> ↓
> Personalized MLP
> ↓
> Gesture classification
> ↓
> Debouncing / cooldown / gesture state
> ↓
> Command mapping
> ↓
> Dispatcher
> ↓
> PyAutoGUI / Windows input
> ↓
> Microsoft PowerPoint
> ```
>
> Determine whether the problem is caused by:
>
> * MLP misclassification
> * unstable predictions between frames
> * incorrect gesture-to-command mapping
> * missing/incorrect debounce or cooldown
> * repeated command execution
> * pointer/annotation state conflicts
> * Windows/PowerPoint input handling
>
> Fix the underlying issue so gesture actions are stable and execute appropriately.
>
> ### 2. Virtual Pointer opens the Print dialog
>
> When I try to use the **Virtual Pointer**, the PowerPoint/Windows **Print dialog repeatedly opens**.
>
> Expected mapping:
>
> ```text
> INDEX + MIDDLE → VIRTUAL_POINTER
> INDEX ONLY      → ANNOTATION_MODE
> ```
>
> The Virtual Pointer must **never trigger `Ctrl + P` / Print**.
>
> Check whether:
>
> * `INDEX_MIDDLE_UP` is being incorrectly classified as `INDEX_UP`
> * the command mapper assigns `ANNOTATION_MODE` instead of `VIRTUAL_POINTER`
> * `Ctrl + P` is being dispatched incorrectly
> * pointer and annotation states interfere with each other
> * the personalized model produces unstable predictions
>
> ### 3. PowerPoint annotation is not working
>
> These annotation features are currently not working:
>
> * **Annotation ON/OFF**
> * **Drawing/pen movement**
> * **Clear Annotation**
>
> Expected flow:
>
> ```text
> INDEX ONLY
> ↓
> ANNOTATION_MODE
> ↓
> PowerPoint pen/annotation mode
> ↓
> Fingertip movement draws on the slide
> ```
>
> `CLEAR_ANNOTATION` should actually remove the annotations from the current PowerPoint slide.
>
> Inspect the complete annotation flow:
>
> ```text
> Gesture
> ↓
> Command mapping
> ↓
> Dispatcher
> ↓
> PyAutoGUI / Windows input
> ↓
> Microsoft PowerPoint
> ```
>
> Make sure annotation does not conflict with Virtual Pointer and does not accidentally trigger `Ctrl + P`.
>
> ### 4. Replace press-to-talk voice with continuous listening
>
> The current voice system requires the user to repeatedly come to the web app and press/enable the microphone before speaking. **Change this completely.**
>
> The microphone should **continuously listen in the background** without requiring the user to enable/disable it for every command.
>
> Use **"Vision" as the wake word/breaker**:
>
> ```text
> Microphone continuously listening
> ↓
> Detect "Vision"
> ↓
> Activate command capture
> ↓
> Listen for command
> ↓
> Detect "OK"
> ↓
> Execute command immediately
> ↓
> Return to continuous listening
> ```
>
> Example:
>
> **"Vision go to next slide OK"**
>
> should immediately execute:
>
> ```text
> NEXT_SLIDE
> ```
>
> The user should **not need to interact with the web app** between commands.
>
> After execution, the microphone should automatically return to wake-word listening:
>
> ```text
> [Listening]
> → "Vision"
> → [Command mode]
> → "go to next slide"
> → "OK"
> → Execute NEXT_SLIDE
> → [Listening again]
> ```
>
> Preserve the existing voice pipeline:
>
> ```text
> Microphone
> ↓
> Faster-Whisper / STT
> ↓
> Text
> ↓
> TF-IDF
> ↓
> Logistic Regression intent model
> ↓
> PowerPoint command
> ```
>
> Do not replace the trained voice model. Integrate the continuous listening and wake-word/`OK` mechanism around the existing system.
>
> ### 5. Make VisionX specifically Windows-focused
>
> **VisionX should be treated as a Windows-specific application for this implementation**, not as a cross-platform application where Windows is just one option.
>
> Optimize and test all platform-dependent functionality specifically for:
>
> **Windows + Microsoft PowerPoint**
>
> Review and correctly implement Windows-specific:
>
> * keyboard shortcuts
> * mouse input
> * PyAutoGUI behavior
> * PowerPoint presentation controls
> * pointer control
> * annotation/pen control
> * microphone permissions/access
> * process/window handling
> * file paths
> * model loading
> * background voice listening
> * application startup
>
> Avoid Linux/macOS-specific assumptions. Where platform-specific behavior is required, implement the **Windows version correctly**.
>
> ### 6. End-to-end testing
>
> Before considering the work complete, test the complete workflow on **Windows with Microsoft PowerPoint**:
>
> ```text
> VisionX
> ↓
> Camera + Microphone
> ↓
> Gesture / Voice AI
> ↓
> Command Dispatcher
> ↓
> Windows Input
> ↓
> Microsoft PowerPoint
> ```
>
> Verify:
>
> * Gesture recognition is stable.
> * Holding a gesture does not repeatedly trigger commands.
> * Slide numbers do not randomly/continuously change.
> * `INDEX + MIDDLE` correctly controls the Virtual Pointer.
> * Virtual Pointer never opens Print.
> * `INDEX ONLY` correctly enables/disables annotation.
> * Fingertip movement correctly draws on slides.
> * Clear Annotation actually clears annotations.
> * Voice continuously listens without repeated UI interaction.
> * `"Vision <command> OK"` executes the command immediately.
> * Voice returns to wake-word listening after each command.
> * Gesture and voice controls do not interfere with each other.
>
> **First inspect the existing code and identify the root causes. Then make the necessary fixes. Do not use disabling features, removing commands, or bypassing the personalized models as workarounds. The final result should be a reliable Windows-specific VisionX application for controlling Microsoft PowerPoint.**
