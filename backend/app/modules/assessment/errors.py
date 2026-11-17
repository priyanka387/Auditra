from app.modules.model_inventory.domain.errors import DomainError


class AssessmentError(DomainError):
    """Base error for AI Risk Assessment (RF-02) operations."""

    code = "ASSESSMENT_ERROR"
    http_status = 400


class AssessmentNotFoundError(AssessmentError):
    code = "ASSESSMENT_NOT_FOUND"
    http_status = 404


class AssessmentQuestionNotFoundError(AssessmentError):
    """The question does not belong to this assessment's template.

    Unknown and out-of-template question ids raise the same 404 so a
    caller can never write an answer into a questionnaire it does not
    own (RF-02 §26).
    """

    code = "ASSESSMENT_QUESTION_NOT_FOUND"
    http_status = 404


class AssessmentInvalidStateError(AssessmentError):
    """The requested operation is not allowed from the current status.

    RF-02 §40: invalid operations return a clear error (e.g. "Cannot
    edit an approved assessment") rather than silently doing nothing.
    """

    code = "ASSESSMENT_INVALID_STATE"
    http_status = 409


class AssessmentIncompleteError(AssessmentError):
    """Submission blocked: required questions are still unanswered."""

    code = "ASSESSMENT_INCOMPLETE"
    http_status = 409


class InvalidAssessmentResponseError(AssessmentError):
    """The value does not match the question's type or options."""

    code = "ASSESSMENT_RESPONSE_INVALID"
    http_status = 400


class InvalidAssessmentQueryError(AssessmentError):
    code = "ASSESSMENT_QUERY_INVALID"
    http_status = 400


class AssessmentTemplateError(DomainError):
    """Base error for assessment-template (RF-02) operations."""

    code = "ASSESSMENT_TEMPLATE_ERROR"
    http_status = 400


class AssessmentTemplateNotFoundError(AssessmentTemplateError):
    code = "ASSESSMENT_TEMPLATE_NOT_FOUND"
    http_status = 404


class AssessmentTemplateInactiveError(AssessmentTemplateError):
    """A deactivated template must not start new assessments (RF-02 §24)."""

    code = "ASSESSMENT_TEMPLATE_INACTIVE"
    http_status = 409


class DuplicateAssessmentTemplateError(AssessmentTemplateError):
    code = "ASSESSMENT_TEMPLATE_DUPLICATE"
    http_status = 409


class InvalidAssessmentTemplateQueryError(AssessmentTemplateError):
    code = "ASSESSMENT_TEMPLATE_QUERY_INVALID"
    http_status = 400
