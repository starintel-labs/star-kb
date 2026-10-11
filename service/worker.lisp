;;; Private bounded JSON pipe shim. No reader/eval and no copied SWI engine.
(require :asdf)
(let ((*standard-output* *error-output*))
  (asdf:load-system "star-logic-adapter-swi"))

(defun object (&rest pairs)
  (let ((table (make-hash-table :test #'equal)))
    (loop for (k v) on pairs by #'cddr do (setf (gethash k table) v)) table))
(defun record (table)
  (loop for k being the hash-keys of table using (hash-value v) collect (cons k v)))
(defun emit (value)
  (yason:encode value *standard-output*) (terpri) (finish-output))
(defun bounded-line ()
  (with-output-to-string (out)
    (loop for count from 0 for c = (read-char *standard-input* nil nil)
          do (cond ((null c) (if (zerop count) (return-from bounded-line nil) (error "EOF")))
                   ((char= c #\Newline) (return))
                   ((>= count 262144) (error "frame limit"))
                   (t (write-char c out))))))

(let* ((backend (starlogicadapterswi:make-swi-backend
                 :swipl-executable (uiop:getenv "STARLANG_SWI_EXECUTABLE")))
       (session (starlogicprotocol:open-logic-session backend "shared-kb")))
  (unwind-protect
       (loop for line = (bounded-line) while line do
         (handler-case
             (let* ((request (yason:parse line)) (op (gethash "operation" request)))
               (cond
                 ((equal op "snapshot")
                  (multiple-value-bind (state digest)
                      (starlogicprotocol:apply-logic-fact-delta
                       backend session
                       (list (cons "relations" (mapcar #'record (gethash "rows" request)))))
                    (declare (ignore state))
                    (emit (object "state" "ready" "snapshotDigest" digest
                                  "ruleDigest" (starlogicadapterswi::swi-backend-bootstrap-digest backend)
                                  "engineBuild" (starlogicadapterswi::swi-backend-build-id backend)))))
                 ((equal op "query")
                  (let ((operation (starlogicprotocol:invoke-logic-operation
                                    backend session "relation-neighbors"
                                    (record (gethash "parameters" request))
                                    :limits (record (gethash "limits" request))))
                        (rows '()))
                    (loop
                      (multiple-value-bind (row state)
                          (starlogicprotocol:next-logic-result backend session operation)
                        (if (eq state :answer)
                            (push (apply #'object (loop for (k . v) in row append (list k v))) rows)
                            (progn (emit (object "state" (string-downcase (symbol-name state))
                                                 "rows" (coerce (nreverse rows) 'vector))) (return)))))))
                 (t (emit (object "error" "invalid_request")))))
           (starlogicadapterswi::swi-query-limit-error () (emit (object "error" "query_limit")))
           (error () (emit (object "error" "engine_failure")))))
    (starlogicprotocol:close-logic-session backend session)))
