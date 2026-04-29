spec 0.1

workflow echo

  external_input topic text

  max_total_steps 10

  model mock-llm

  role responder
    prompt file "prompts/responder.md"

  artifact response text

  state respond
    actor model mock-llm
    role responder
    prompt template "prompts/responder.md" with topic
    reads topic
    writes response text
    on complete => confirm
    on error => stop
    on timeout => stop

  state confirm
    actor human
    prompt file "prompts/confirm.md"
    reads response
    options accept, reject
    on accept => done
    on reject => stop
    on timeout => stop
    on cancelled => stop
