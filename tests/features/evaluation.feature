Feature: Agent Evidence Evaluation Constraint
    In order to maintain strict auditing standards
    As an Audit Reviewer
    I want to ensure the AI Swarm never passes a control if the evidence is missing

    Scenario: Missing Evidence always results in Failure or Exception
        Given the worker agent is evaluating a control "IAM-01"
        When the provided evidence is empty or missing
        Then the AI evaluation must not return a "Pass" status
